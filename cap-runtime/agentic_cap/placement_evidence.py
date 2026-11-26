"""Bounded post-release observation in the existing two-cube scene.

This moves the empty robot out of a fixed camera's view. It is not camera
planning or a collision certificate for arbitrary scenes or other hardware.
"""
from __future__ import annotations

import copy
import math
import time

from .active_evidence import MAX_VIEWS, SUPPORTED_OBJECTS, observation_targets
from .core import identifier, plain

CLEARANCE_M = .10
ORIGIN_TOLERANCE_M = .01
MAX_CLEARANCE_RETREAT_M = .15


def finite_vector(value, length):
    return (isinstance(value, list) and len(value) == length
            and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                    and math.isfinite(x) for x in value))


def empty_gripper_evidence(snapshot):
    """Use the measured aperture AND the command from the same fresh packet."""
    stamp = snapshot.get('captured_at')
    result = dict(status='unknown', evidence_ref=snapshot.get('evidence_ref'), captured_at=stamp)
    if not isinstance(stamp, (int, float)) or not 0 <= time.time()-stamp <= 1:
        return dict(result, reason='stale_or_missing_gripper_observation')
    positions = snapshot.get('gripper_joint_positions')
    if not finite_vector(positions, 2):
        return dict(result, reason='missing_or_invalid_gripper_measurement')
    width = sum(abs(x) for x in positions)
    result.update(gripper_width_m=width, command=snapshot.get('last_gripper_command'))
    if result['command'] != 'open' or width <= .06:
        return dict(result, reason='open_empty_gripper_not_confirmed')
    return dict(result, status='verified', reason='public_open_aperture; bounded_cube_scope')


def scene_ceiling(snapshot):
    heights = [obj['surface_q95_xyz'][2] for obj in snapshot.get('objects', {}).values()
               if obj.get('status') == 'visible' and finite_vector(obj.get('surface_q95_xyz'), 3)]
    return max(heights) if heights else None


def remember_release(backend, segment, snapshot, emit):
    e = backend.episode
    marker = dict(id=identifier(), goal=copy.deepcopy(e.goal),
                  release_tcp_xyz=copy.deepcopy(snapshot.get('tcp_xyz')),
                  release_tcp_wxyz=copy.deepcopy(snapshot.get('tcp_wxyz')),
                  scene_ceiling_m=scene_ceiling(snapshot),
                  evidence_ref=snapshot.get('evidence_ref'),
                  release_step=backend.tick, targets=None, tried=[], blocked=False)
    event = emit('placement_released', release_id=marker['id'],
                 release_tcp_xyz=marker['release_tcp_xyz'],
                 evidence_ref=marker['evidence_ref'], release_step=marker['release_step'])
    marker['event_id'] = event['id'] if isinstance(event, dict) else len(getattr(e, 'events', []))
    backend.placement_release = marker


def clearance_evidence(backend, snapshot, marker):
    e = backend.episode
    result = dict(status='unknown', empty_gripper=empty_gripper_evidence(snapshot))
    if result['empty_gripper']['status'] != 'verified':
        return dict(result, reason='empty_gripper_unverified')
    if not marker or marker['goal'] != e.goal:
        return dict(result, reason='no_current_placement_release')
    if any(event['kind'] == 'goal_changed' and event['id'] > marker['event_id'] for event in e.events):
        return dict(result, reason='release_superseded_by_goal_change')
    if snapshot.get('control_step', -1) <= marker['release_step']:
        return dict(result, reason='observation_not_after_release')
    tcp, origin = snapshot.get('tcp_xyz'), marker['release_tcp_xyz']
    quat, release_quat = snapshot.get('tcp_wxyz'), marker['release_tcp_wxyz']
    if not all((finite_vector(tcp, 3), finite_vector(origin, 3),
                finite_vector(quat, 4), finite_vector(release_quat, 4))):
        return dict(result, reason='invalid_public_tcp_pose')
    norm = math.sqrt(sum(x*x for x in quat) * sum(x*x for x in release_quat))
    if norm == 0 or abs(sum(a*b for a, b in zip(quat, release_quat)))/norm < math.cos(math.radians(5)):
        return dict(result, reason='release_orientation_changed')
    ceilings = [x for x in (marker['scene_ceiling_m'], scene_ceiling(snapshot)) if x is not None]
    if not ceilings:
        return dict(result, reason='missing_public_scene_height')
    required = max(origin[2], max(ceilings)) + CLEARANCE_M
    result.update(required_tcp_z_m=required, actual_tcp_xyz=tcp, release_id=marker['id'])
    # Before establishing a lateral path the retreat must have been vertical.
    # Later views stay on that path's X/Z plane and keep its orientation.
    path_origin = marker.get('origin')
    if path_origin is None and math.dist(tcp[:2], origin[:2]) > ORIGIN_TOLERANCE_M:
        return dict(result, reason='vertical_retreat_origin_changed')
    if path_origin is not None and (abs(tcp[0]-path_origin[0]) > ORIGIN_TOLERANCE_M
                                    or abs(tcp[2]-path_origin[2]) > ORIGIN_TOLERANCE_M):
        return dict(result, reason='inspection_origin_changed')
    if tcp[2] < required - 1e-6:
        return dict(result, reason='vertical_retreat_not_confirmed')
    return dict(result, status='available', reason='bounded_cube_post_release_clearance')


def clearance_retreat_target(snapshot, marker, gate):
    """Compose strict clearance with the existing measured arrival tolerance.

    Only the release column, before any lateral inspection, admits this bounded
    upward motion. A commanded endpoint is never accepted as clearance evidence.
    """
    from .cartesian_motion import MOTION_PROTOCOL

    tolerance = MOTION_PROTOCOL['position_tolerance_m']
    if (gate.get('reason') != 'vertical_retreat_not_confirmed' or not marker
            or marker.get('origin') is not None or marker.get('tried')
            or marker.get('clearance_attempted') or marker.get('blocked')):
        return None
    tcp = snapshot['tcp_xyz']
    if tcp[2] < marker['release_tcp_xyz'][2] - tolerance:
        return None
    target = list(tcp)
    target[2] = gate['required_tcp_z_m'] + 2*tolerance
    if not 0 < target[2]-tcp[2] <= MAX_CLEARANCE_RETREAT_M:
        return None
    return target


def install_placement_inspector(backend, segment, functions, *, call, fresh_after, emit):
    e = backend.episode

    def inspect(top, support):
        if top not in SUPPORTED_OBJECTS or support not in SUPPORTED_OBJECTS or top == support:
            raise ValueError('Placement inspection requires distinct supported cubes')
        e.checkpoint(segment)
        if e.goal.get('on') != [top, support]:
            raise ValueError('Placement inspection must match the current final relation')
        started, initial = time.monotonic(), dict(e.used)
        result = dict(status='unknown', acquisition_id=identifier(),
                      acquisition_type='placement_verification', top=top, support=support,
                      revision=segment['revision'], views_used=0, reason='inspection_not_finished')
        marker = getattr(backend, 'placement_release', None)
        emit('evidence_acquisition_started', **result)

        def verify_current():
            readiness = e._wait_for_evidence(segment, [dict(on=[top, support], empty_gripper=True)])
            if readiness['status'] not in {'ready', 'not_required'}:
                return dict(status='unknown', reason='post_action_window_unavailable', readiness=readiness)
            return e.verify(dict(on=[top, support], empty_gripper=True))

        try:
            snapshot = e.observe()
            empty = empty_gripper_evidence(snapshot)
            result['empty_gripper'] = empty
            if empty['status'] != 'verified':
                result['reason'] = 'empty_gripper_unverified'
                return result
            verification = verify_current()
            result.update(before=verification, goal_verification=verification,
                          evidence_ref=verification.get('evidence_ref'), captured_at=verification.get('captured_at'))
            if verification['status'] != 'unknown':
                result.update(status=verification['status'], reason='current_relation_observable')
                return result
            snapshot = e.observe()
            gate = clearance_evidence(backend, snapshot, marker)
            result['clearance'] = gate
            target = clearance_retreat_target(snapshot, marker, gate)
            if target is not None:
                with e.lock:
                    e.checkpoint(segment)
                    marker['clearance_attempted'] = True
                used, retreat_start = dict(e.used), time.monotonic()
                row = dict(acquisition_id=result['acquisition_id'], release_id=marker['id'],
                           target_xyz=target, target_wxyz=copy.deepcopy(snapshot['tcp_wxyz']),
                           before=gate, before_evidence_ref=snapshot.get('evidence_ref'))
                emit('placement_clearance_retreat_started', **row)
                call('goto_pose', functions['goto_pose'], target, row['target_wxyz'],
                     parent_api='inspect_placement')
                e.checkpoint(segment)
                snapshot = fresh_after(max(snapshot['control_step'], backend.tick))
                e.checkpoint(segment)
                gate = clearance_evidence(backend, snapshot, marker)
                result.update(clearance=gate, clearance_retreat_used=True,
                              evidence_ref=snapshot.get('evidence_ref'),
                              captured_at=snapshot.get('captured_at'))
                emit('placement_clearance_retreat_finished', **row, after=gate,
                     evidence_ref=snapshot.get('evidence_ref'), actual_tcp_xyz=snapshot.get('tcp_xyz'),
                     actual_tcp_wxyz=snapshot.get('tcp_wxyz'),
                     elapsed_seconds=time.monotonic()-retreat_start,
                     used_delta={key: e.used[key]-used[key] for key in used})
                if gate['status'] != 'available':
                    marker['blocked'] = True
                else:
                    verification = verify_current()
                    result.update(goal_verification=verification,
                                  evidence_ref=verification.get('evidence_ref'),
                                  captured_at=verification.get('captured_at'))
                    if verification['status'] != 'unknown':
                        result.update(status=verification['status'], reason='relation_observable_after_clearance')
                        return result
            if gate['status'] != 'available':
                result['reason'] = gate['reason']
                return result
            if marker['blocked']:
                result['reason'] = 'prior_inspection_interrupted_or_failed'
                return result
            if marker['targets'] is None:
                marker.update(origin=copy.deepcopy(snapshot['tcp_xyz']),
                              targets=observation_targets(snapshot['tcp_xyz']))
            orientation = copy.deepcopy(snapshot['tcp_wxyz'])
            for target in marker['targets']:
                if target in marker['tried'] or len(marker['tried']) >= MAX_VIEWS:
                    continue
                e.checkpoint(segment)
                gate = clearance_evidence(backend, e.observe(), marker)
                if gate['status'] != 'available':
                    result.update(reason=gate['reason'], clearance=gate)
                    return result
                marker['tried'].append(target)
                result['views_used'] += 1
                before = e.observe()
                used, view_start = dict(e.used), time.monotonic()
                row = dict(acquisition_id=result['acquisition_id'], acquisition_type=result['acquisition_type'],
                           view_index=len(marker['tried']), release_id=marker['id'], target_xyz=target,
                           target_wxyz=orientation, before=verification,
                           actual_before_tcp_xyz=before.get('tcp_xyz'))
                emit('view_attempt', **row)
                call('goto_pose', functions['goto_pose'], target, orientation, parent_api='inspect_placement')
                snapshot = fresh_after(max(before['control_step'], backend.tick))
                empty = empty_gripper_evidence(snapshot)
                result['empty_gripper'] = empty
                if empty['status'] != 'verified':
                    marker['blocked'] = True
                    verification = dict(status='unknown', reason='empty_gripper_lost_during_inspection')
                else:
                    verification = verify_current()
                    # Verification may wait for a window; do not retain a
                    # pre-window frame as the reported evidence source.
                    snapshot = e.observe()
                    empty = empty_gripper_evidence(snapshot)
                    if empty['status'] != 'verified':
                        marker['blocked'] = True
                        verification = dict(status='unknown', reason='empty_gripper_lost_during_inspection')
                result['empty_gripper'] = empty
                result.update(goal_verification=verification, evidence_ref=verification.get('evidence_ref', snapshot.get('evidence_ref')),
                              captured_at=verification.get('captured_at', snapshot.get('captured_at')), actual_tcp_xyz=snapshot.get('tcp_xyz'))
                emit('view_result', **row, after=verification, empty_gripper=empty,
                     actual_tcp_xyz=snapshot.get('tcp_xyz'), actual_tcp_wxyz=snapshot.get('tcp_wxyz'),
                     elapsed_seconds=time.monotonic()-view_start,
                     used_delta={key: e.used[key]-used[key] for key in used})
                if empty['status'] != 'verified' or verification['status'] != 'unknown':
                    result.update(status=verification['status'], reason=verification.get('reason', 'public_relation_checked'))
                    return result
            result['reason'] = 'placement_remains_unknown; views_exhausted'
            return result
        except Exception as exc:
            if marker:
                marker['blocked'] = True
            result.update(status='unknown', reason=str(exc), error_code=getattr(exc, 'code', None))
            raise
        finally:
            snapshot = e.observe()
            if result.get('evidence_ref') is None:
                result['evidence_ref'] = snapshot.get('evidence_ref')
            if result.get('captured_at') is None:
                result['captured_at'] = snapshot.get('captured_at')
            result.update(total_views_used=len(marker['tried']) if marker else 0,
                          elapsed_seconds=time.monotonic()-started,
                          used_delta={key: e.used[key]-initial[key] for key in initial})
            with e.lock:
                segment['evidence_acquisition'] = plain(result)
                emit('evidence_acquisition_finished', **result)

    return inspect
