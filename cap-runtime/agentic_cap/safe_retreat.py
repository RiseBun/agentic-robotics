"""Allow a requested reversal of the last observed vertical descent only.

This is a recovery exception, not placement permission or a collision guarantee
for changing scenes. It neither schedules motion nor changes the gripper.
"""
import copy
import inspect
import math

from .placement_evidence import finite_vector

XY_TOLERANCE_M = .005
MAX_DESCENT_M = .15


def aligned(a, b):
    if not finite_vector(a, 4) or not finite_vector(b, 4):
        return False
    norm = math.sqrt(sum(x*x for x in a)*sum(x*x for x in b))
    return norm > 0 and abs(sum(x*y for x, y in zip(a, b)))/norm >= math.cos(math.radians(.5))


def motion_arguments(function, args, kwargs):
    try:
        bound = inspect.signature(function).bind(*args, **kwargs)
        values = list(bound.arguments.values())
        return values[0], values[1], bound.arguments.get('z_approach', 0.)
    except (ValueError, TypeError, IndexError):
        return None, None, None


def descent_anchor(backend, snapshot, target, orientation):
    e = backend.episode
    top = e.goal.get('on', [None])[0]
    start = snapshot.get('tcp_xyz')
    if (not finite_vector(start, 3) or not finite_vector(target, 3)
            or math.dist(start[:2], target[:2]) > XY_TOLERANCE_M
            or not .005 < start[2]-target[2] <= MAX_DESCENT_M
            or not aligned(snapshot.get('tcp_wxyz'), orientation)
            or snapshot.get('last_gripper_command') != 'closed'
            or getattr(backend, 'holding_results', {}).get(top) != 'verified'):
        return None
    return dict(goal=copy.deepcopy(e.goal), object_name=top, start_xyz=copy.deepcopy(start),
                end_xyz=copy.deepcopy(target), orientation=copy.deepcopy(orientation),
                evidence_ref=snapshot.get('evidence_ref'))


def retreat_evidence(backend, snapshot, anchor, target=None, orientation=None, approach=0.):
    e = backend.episode
    result = dict(status='unknown', reason='no_valid_recorded_vertical_descent')
    if not anchor or anchor['goal'] != e.goal:
        return result
    if any(event['kind'] == 'goal_changed' and event['id'] > anchor['event_id'] for event in e.events):
        return dict(result, reason='descent_superseded_by_goal_change')
    if e.gripper >= .5 or getattr(backend, 'held_hint', None) != anchor['object_name']:
        return dict(result, reason='closed_grasp_context_lost')
    if snapshot.get('control_step', -1) <= anchor['completed_step']:
        return dict(result, reason='observation_not_after_descent')
    stamp, current = snapshot.get('captured_at', 0), snapshot.get('tcp_xyz')
    import time
    if (not isinstance(stamp, (int, float)) or not 0 <= time.time()-stamp <= 1
            or snapshot.get('last_gripper_command') != 'closed'
            or not finite_vector(current, 3) or not aligned(snapshot.get('tcp_wxyz'), anchor['orientation'])):
        return dict(result, reason='current_retreat_pose_unverified')
    if (math.dist(current[:2], anchor['start_xyz'][:2]) > XY_TOLERANCE_M
            or not anchor['end_xyz'][2]-.01 <= current[2] < anchor['start_xyz'][2]-.005):
        return dict(result, reason='outside_recorded_vertical_path')
    result.update(target_xyz=copy.deepcopy(anchor['start_xyz']), target_wxyz=copy.deepcopy(anchor['orientation']),
                  evidence_ref=anchor['evidence_ref'], scope='retrace last public vertical descent; bounded cube scene')
    if target is not None and (not finite_vector(target, 3)
            or math.dist(current[:2], target[:2]) > XY_TOLERANCE_M
            or not current[2]+.005 < target[2] <= anchor['start_xyz'][2]+.001
            or not aligned(orientation, anchor['orientation']) or approach != 0.):
        return dict(result, reason='requested_motion_is_not_a_bounded_vertical_retreat')
    return dict(result, status='available', reason='recorded_vertical_reversal_only')
