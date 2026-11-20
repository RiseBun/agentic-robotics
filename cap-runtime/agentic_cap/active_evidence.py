"""Bounded, public-evidence-only support inspection for cube placement."""
from __future__ import annotations

import math
import copy
import time

SUPPORTED_OBJECTS = {"red cube", "green cube"}
MAX_VIEWS = 2
VIEW_OFFSET_M = 0.10
MAX_ABS_Y_M = 0.22


def support_evidence(snapshot, support, reference=None):
    if snapshot.get('camera_views'):
        from .public_views import choose_support_view
        return choose_support_view(snapshot, support, reference, _single_support_evidence)
    return _single_support_evidence(snapshot, support, reference)


def _single_support_evidence(snapshot, support, reference=None):
    """Classify public support geometry without claiming center accuracy."""
    result = {
        "status": "unknown", "object_name": support,
        "evidence_ref": snapshot.get("evidence_ref"),
        "scope": "public RGB-D support visibility; center not independently verified",
    }
    stamp = snapshot.get('captured_at')
    result.update(captured_at=stamp, control_step=snapshot.get('control_step'))
    if not isinstance(stamp, (float, int)) or not 0 <= time.time()-stamp <= 1:
        return {**result, 'reason': 'stale_or_missing_observation'}
    obj = (snapshot.get("objects") or {}).get(support, {})
    if obj.get("status") != "visible":
        return {**result, "reason": "support_not_visible"}
    position = obj.get('surface_median_xyz')
    if (not isinstance(position, list) or len(position) != 3
            or any(not isinstance(x, (int, float)) or not math.isfinite(x) for x in position)):
        return {**result, 'reason': 'invalid_support_position'}
    spans = obj.get("surface_span_xyz")
    if not isinstance(spans, list) or len(spans) != 3:
        return {**result, "reason": "invalid_visible_spans"}
    if any(not isinstance(x, (int, float)) or not math.isfinite(x) or x <= 0 for x in spans):
        return {**result, "reason": "invalid_visible_spans"}
    ref_obj = (reference or {}).get("objects", {}).get(support, {})
    ref_spans = ref_obj.get("surface_span_xyz")
    ref_scale = max(ref_spans) if isinstance(ref_spans, list) and len(ref_spans) == 3 else 0.0
    scale = max(max(spans), ref_scale)
    ratio = min(spans[:2]) / scale if scale else 0.0
    result.update(horizontal_span_ratio=ratio, observed_scale_m=scale,
                  visible_depth_pixels=obj.get("valid_depth_pixels"))
    if ratio < 0.4:
        return {**result, "reason": "support_horizontal_extent_truncated"}
    return {**result, "status": "available", "reason": "visible_box_support; not_center_verified"}


def observation_targets(tcp_xyz):
    """Return at most two bounded lateral inspection poses."""
    if not isinstance(tcp_xyz, list) or len(tcp_xyz) != 3:
        raise ValueError("Invalid public TCP position")
    if any(not isinstance(x, (int, float)) or not math.isfinite(x) for x in tcp_xyz):
        raise ValueError("Invalid public TCP position")
    direction = -1 if tcp_xyz[1] > 0 else 1
    targets = []
    for sign in (direction, -direction):
        target = list(tcp_xyz)
        target[1] += sign * VIEW_OFFSET_M
        if abs(target[1]) <= MAX_ABS_Y_M:
            targets.append(target)
    return targets[:MAX_VIEWS]


def install_inspector(backend, segment, functions):
    """Compose existing cancellable primitives; include every nested call in budgets."""
    from .core import Halt, identifier, plain
    from .feedback import EffectError, feedback
    e = backend.episode
    tools = dict(functions)
    sessions = getattr(backend, 'inspection_sessions', None)
    if sessions is None:
        sessions = {}
        backend.inspection_sessions = sessions
    holding_results = getattr(backend, 'holding_results', None)
    if holding_results is None:
        holding_results = {}
        backend.holding_results = holding_results

    def checkpoint():
        e.checkpoint(segment)

    def emit(kind, **data):
        # A cancelled RPC worker may finish after a new segment starts. Bind
        # its events to the original segment rather than e.active at that time.
        data.setdefault('revision', segment['revision'])
        return e.event(kind, segment_id=segment.get('id'), **data)

    def call(name, function, *args, parent_api='inspect_support'):
        with e.lock:
            checkpoint()
            if e.used['calls'] >= e.limits['calls']:
                raise Halt('Public API budget exhausted', 'BUDGET_EXHAUSTED')
            e.used['calls'] += 1
            if name == 'goto_pose':
                backend.safe_retreat = None
            identity = identifier()
            emit('api_started', call_id=identity, name=name, args=args, kwargs={}, parent_api=parent_api)
        try:
            result = plain(function(*args))
            checkpoint()
            emit('api_result', call_id=identity, name=name, result=result)
            if isinstance(result, dict) and result.get('status') in {'failed', 'unknown'}:
                raise EffectError(result)
            return result
        except Exception as exc:
            emit('api_error', call_id=identity, name=name, error=str(exc),
                    feedback=feedback(exc, api=name, call_id=identity, observation=e.observe()))
            raise

    def checked_holding(name):
        value = functions['check_holding'](name)
        checkpoint()
        if value['status'] == 'verified':
            backend.held_hint = name
            arm = getattr(backend, 'arm_holding_watchdog', None)
            if arm is not None:
                arm(name, e.observe())
        elif getattr(backend, 'held_hint', None) == name:
            backend.held_hint = None
            backend.safe_retreat = None
        holding_results[name] = value['status']
        return value

    def fresh_after(step):
        end = time.monotonic()+3
        with e.cv:
            while True:
                checkpoint()
                value = e.observe()
                if value.get('control_step', -1) > step and 0 <= time.time()-value.get('captured_at', 0) <= 1:
                    return value
                if time.monotonic() >= end:
                    raise EffectError(dict(status='unknown', reason='fresh_observation_timeout'))
                e.cv.wait(.05)

    def current_observation():
        value = e.observe()
        action_step = getattr(backend, 'last_action_tick', 0)
        if action_step and value.get('control_step', -1) <= action_step:
            return fresh_after(action_step)
        return value

    def inspect(support, held_object):
        if support not in SUPPORTED_OBJECTS or held_object not in SUPPORTED_OBJECTS or support == held_object:
            raise ValueError('Inspection requires distinct supported cubes')
        checkpoint()
        start = time.monotonic()
        initial_used = dict(e.used)
        before = e.observe()
        key = (support, held_object)
        session = sessions.setdefault(key, dict(targets=observation_targets(before.get('tcp_xyz', [])), tried=[], blocked=False))
        views, evidence = 0, support_evidence(before, support, backend.reference)
        result = dict(status='unknown', acquisition_id=identifier(), revision=segment['revision'], support=support, held_object=held_object,
                      reason='inspection_not_finished', before=evidence)
        emit('evidence_acquisition_started', **result, views_remaining=max(0, MAX_VIEWS-len(session['tried'])))
        try:
            if session['blocked']:
                result['reason'] = 'prior_inspection_failed; new_grasp_required'
                return result
            # Holding returns three-valued evidence. Unlike motion errors,
            # retain this result so the acquisition can report failed/unknown.
            held = checked_holding_counted(held_object)
            result['holding'] = held
            if held['status'] != 'verified':
                session['blocked'] = True
                result.update(status='failed' if held['status'] == 'failed' else 'unknown', reason='holding_precondition_unverified')
                return result
            current = e.observe()
            evidence = support_evidence(current, support, backend.reference)
            if evidence['status'] == 'available':
                result.update(status='available', reason='already_available')
                return result
            # Stale data needs a fresh sensor packet, never a blind movement.
            if evidence['reason'] == 'stale_or_missing_observation':
                result['reason'] = evidence['reason']
                return result
            from .safe_retreat import retreat_evidence
            retreat = retreat_evidence(backend, current, getattr(backend, 'safe_retreat', None))
            if retreat['status'] == 'available':
                # Do not turn a failed low placement into a lateral shove.
                # No view has run here, so the recorded retreat can establish
                # a new inspection origin without resetting spent views.
                if not session['tried']:
                    session['pending_retreat'] = retreat['target_xyz']
                result.update(reason='retract_before_lateral_inspection', recovery=retreat)
                return result
            pending = session.get('pending_retreat')
            if pending is not None and not session['tried'] and math.dist(current.get('tcp_xyz', []), pending) <= .01:
                session['targets'] = observation_targets(current['tcp_xyz'])
                session.pop('pending_retreat')
            orientation = copy.deepcopy(current.get('tcp_wxyz'))
            if not isinstance(orientation, list) or len(orientation) != 4 or any(not math.isfinite(x) for x in orientation):
                raise ValueError('Invalid public TCP orientation')
            for target in session['targets']:
                if target in session['tried']:
                    continue
                checkpoint()
                # A caller moving to another height must begin a new grasp,
                # rather than reusing an old inspection path through objects.
                tcp = current.get('tcp_xyz', [])
                if len(tcp) != 3 or abs(tcp[0]-target[0]) > .01 or abs(tcp[2]-target[2]) > .01:
                    result['reason'] = 'inspection_origin_changed'
                    return result
                session['tried'].append(target)
                views += 1
                before_step = current.get('control_step', -1)
                row = dict(view_index=len(session['tried']), target_xyz=target, target_wxyz=orientation,
                           before=evidence, actual_before_tcp_xyz=tcp)
                emit('view_attempt', acquisition_id=result['acquisition_id'], support=support, held_object=held_object, **row)
                view_start, view_used = time.monotonic(), dict(e.used)
                call('goto_pose', functions['goto_pose'], target, orientation)
                # Require a frame after motion completion, not an image that
                # merely follows the pre-motion frame while still in flight.
                completed_step = max(before_step, getattr(backend, 'tick', before_step))
                held = checked_holding_counted(held_object)
                current = fresh_after(completed_step)
                evidence = support_evidence(current, support, backend.reference)
                emit('view_result', acquisition_id=result['acquisition_id'], support=support, held_object=held_object, **row,
                        after=evidence, holding=held, actual_tcp_xyz=current.get('tcp_xyz'),
                        actual_tcp_wxyz=current.get('tcp_wxyz'),
                        elapsed_seconds=time.monotonic()-view_start,
                        used_delta={k: e.used[k]-view_used[k] for k in view_used})
                result.update(holding=held, after=evidence, actual_tcp_xyz=current.get('tcp_xyz'),
                              actual_tcp_wxyz=current.get('tcp_wxyz'))
                if held['status'] != 'verified':
                    session['blocked'] = True
                    result.update(status='failed' if held['status'] == 'failed' else 'unknown', reason='holding_not_preserved')
                    return result
                if evidence['status'] == 'available':
                    result.update(status='available', reason='public_support_evidence_available')
                    return result
            result['reason'] = 'support_remains_unobservable; views_exhausted'
            return result
        except Exception as exc:
            session['blocked'] = True
            current = e.observe()
            evidence = support_evidence(current, support, backend.reference)
            result.update(status='unknown', reason=str(exc), error_code=getattr(exc, 'code', None),
                          after=evidence, actual_tcp_xyz=current.get('tcp_xyz'),
                          actual_tcp_wxyz=current.get('tcp_wxyz'))
            raise
        finally:
            result.update(views_used=views, total_views_used=len(session['tried']),
                          evidence_ref=evidence.get('evidence_ref'), captured_at=evidence.get('captured_at'),
                          elapsed_seconds=time.monotonic()-start,
                          used_delta={k: e.used[k]-initial_used[k] for k in initial_used})
            with e.lock:
                segment['evidence_acquisition'] = plain(result)
                emit('evidence_acquisition_finished', **result)

    def release(*args, **kwargs):
        checkpoint()
        goal = e.goal
        before = current_observation()
        final_release = (e.gripper < .5 and 'on' in goal
                         and getattr(backend, 'held_hint', None) == goal['on'][0])
        # Unknown holding identity is not an exemption. An explicitly failed
        # grasp can be opened for recovery; the verified other cube may be
        # cleared during restack. Redundant close calls cannot reset this gate.
        if (e.gripper < .5 and 'on' in goal
                and getattr(backend, 'held_hint', None) in {None, goal['on'][0]}
                and holding_results.get(goal['on'][0]) != 'failed'):
            evidence = support_evidence(before, goal['on'][1], backend.reference)
            emit('placement_release_checked', evidence=evidence,
                 holding_identity=getattr(backend, 'held_hint', None),
                 allowed=evidence['status'] == 'available')
            if evidence['status'] != 'available':
                from .safe_retreat import retreat_evidence
                recovery = retreat_evidence(backend, before, getattr(backend, 'safe_retreat', None))
                raise EffectError(dict(status='unknown', reason='Support evidence unknown; fresh support evidence required',
                                       evidence=evidence, recovery=recovery))
        result = functions['open_gripper'](*args, **kwargs)
        checkpoint()
        if isinstance(result, dict) and result.get('status') in {'unknown', 'failed'}:
            raise EffectError(result)
        with e.lock:
            checkpoint()
            if final_release:
                from .placement_evidence import remember_release
                remember_release(backend, segment, before, emit)
            backend.held_hint = None
            holding_results.clear()
            sessions.clear()
        return result

    def grasp(*args, **kwargs):
        with e.lock:
            checkpoint()
            backend.placement_release = None
            backend.safe_retreat = None
            # A new close may acquire an object even if the command later
            # fails or times out. Prior failed-grasp evidence cannot authorize
            # opening that new, unverified grasp. Preserve verified identity
            # and inspection budgets when closing an already held object.
            if getattr(backend, 'held_hint', None) is None and holding_results:
                prior = dict(holding_results)
                holding_results.clear()
                emit('holding_evidence_invalidated', reason='new_grasp_attempt', previous_results=prior)
        result = functions['close_gripper'](*args, **kwargs)
        checkpoint()
        return result

    def checked_holding_counted(name):
        # call() aborts on unknown/failed effects; check evidence itself must
        # remain inspectable so the acquisition returns its structured status.
        try:
            return call('check_holding', checked_holding, name)
        except EffectError as exc:
            return exc.result

    def guarded_motion(*args, **kwargs):
        checkpoint()
        goal = e.goal
        from .safe_retreat import descent_anchor, motion_arguments, retreat_evidence
        target, orientation, approach = motion_arguments(functions['goto_pose'], args, kwargs)
        anchor = None
        if 'on' in goal and getattr(backend, 'held_hint', None) == goal['on'][0]:
            current = current_observation()
            evidence = support_evidence(current, goal['on'][1], backend.reference)
            if evidence['status'] != 'available':
                retreat = retreat_evidence(backend, current, getattr(backend, 'safe_retreat', None), target, orientation, approach)
                allowed = retreat['status'] == 'available' and target is not None
                emit('placement_retreat_checked', evidence=evidence, retreat=retreat, allowed=allowed)
                if not allowed:
                    raise EffectError(dict(status='unknown', reason='Support evidence unknown; acquire evidence before placement motion',
                                           evidence=evidence, recovery=retreat))
                backend.safe_retreat = None  # Consume even when a motion attempt fails.
                return functions['goto_pose'](*args, **kwargs)
            emit('placement_motion_checked', evidence=evidence, allowed=True)
            anchor = descent_anchor(backend, current, target, orientation)
        backend.safe_retreat = None
        result = functions['goto_pose'](*args, **kwargs)
        with e.lock:
            checkpoint()
            if anchor and not (isinstance(result, dict) and result.get('status') in {'failed', 'unknown'}):
                event = emit('vertical_descent_recorded', start_xyz=anchor['start_xyz'], end_xyz=anchor['end_xyz'],
                             evidence_ref=anchor['evidence_ref'])
                anchor['event_id'] = event['id']
                anchor['completed_step'] = getattr(backend, 'tick', 0)
                backend.safe_retreat = anchor
        return result

    tools.update(inspect_support=inspect, check_holding=checked_holding,
                 open_gripper=release, close_gripper=grasp, goto_pose=guarded_motion,
                 get_execution_state=current_observation)
    from .placement_evidence import install_placement_inspector
    tools['inspect_placement'] = install_placement_inspector(
        backend, segment, functions, call=call, fresh_after=fresh_after, emit=emit)
    return tools
