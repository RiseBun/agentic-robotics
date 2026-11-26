"""Public, task-local memory of a rejected placement route across safe retreats.

This is a repetition heuristic, not a verifier or diagnosis of physical cause.
No frame IDs, timestamps, held-object world positions or simulator truth are used.
"""
import math
from .skills import selected_support_geometry

POSITION_TOLERANCE_M = .01
ORIENTATION_TOLERANCE_RAD = math.radians(5)


def vector(value, size):
    return (list(value) if isinstance(value, (list, tuple)) and len(value) == size
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in value) else None)


def placement_context(snapshot, facts, arguments, segments=None):
    obj, support = arguments['object'], arguments['support']
    surface = selected_support_geometry(snapshot, support)
    holding = facts.get('holding_geometry:'+obj, {}).get('result', {}).get('geometry') or {}
    result = dict(support_min=vector(surface.get('surface_q05_xyz'), 3),
                  support_max=vector(surface.get('surface_q95_xyz'), 3),
                  offset=vector(holding.get('offset_xyz'), 3), quaternion=vector(holding.get('tcp_wxyz'), 4),
                  cameras=sorted({'robot0_robotview', *snapshot.get('camera_views', {}).keys()}))
    acquisitions = [s['id'] for s in (segments or {}).values()
                    if (s.get('task_binding') or {}).get('node_id', '').endswith(':acquire')
                    and (s.get('node_verification') or {}).get('status') == 'verified'
                    and s.get('execution') == 'completed']
    result['grasp_attempt'] = acquisitions[-1] if acquisitions else None
    return result if all(result[k] is not None for k in ('support_min', 'support_max', 'offset', 'quaternion')) else None


def same_route_context(before, current):
    if not before or not current or before['cameras'] != current['cameras']:
        return False
    if before.get('grasp_attempt') != current.get('grasp_attempt'):
        return False
    # A side becoming newly visible changes a bounding edge without moving the
    # support. Compare measured box centres, not any one truncated edge. Offset
    # estimates within the same verified grasp are not a new grasp strategy.
    centres = [[(lo+hi)/2 for lo,hi in zip(c['support_min'],c['support_max'])] for c in (before,current)]
    if any(abs(a-b) > POSITION_TOLERANCE_M for a,b in zip(*centres)):
        return False
    a, b = before['quaternion'], current['quaternion']
    norm = math.sqrt(sum(v*v for v in a)*sum(v*v for v in b))
    return norm > 1e-8 and abs(sum(x*y for x, y in zip(a, b)))/norm >= math.cos(ORIENTATION_TOLERANCE_RAD/2)


def retry_constraint(attempts, context, support):
    for attempt in reversed(attempts):
        feedback = attempt.get('feedback') or {}
        evidence = (feedback.get('evidence') or {}).get('evidence') or {}
        # Only a recorded release rejection for this support constrains this route.
        # Program errors, cancelled motions and unknown final verification do not.
        if (attempt.get('execution') == 'failed' and feedback.get('category') == 'insufficient_evidence'
                and feedback.get('api') == 'open_gripper' and evidence.get('status') == 'unknown'
                and evidence.get('object_name') == support
                and same_route_context((attempt.get('task_binding') or {}).get('retry_context'), context)):
            return dict(status='requires_changed_placement_evidence', segment_id=attempt['id'],
                        reason='same_placement_route_previously_lost_support_evidence_at_release',
                        release_evidence_ref=evidence.get('evidence_ref'),
                        scope='Public route repetition heuristic; retreat is safety progress, not a repair of the release view. '
                              'A new frame, revision or retreat alone does not authorize the same route. '
                              'Changed public support geometry, grasp offset/orientation or camera coverage requires fresh Runtime checks.')
    return None
