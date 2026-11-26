"""Explain recorded reverse-path admission without changing its predicates."""
import numpy as np


def assess_recorded_descent(record,obj,revision,encoder,snapshot):
    checks={'record_present':record is not None}
    details={}
    if record is not None:
        end=np.array(record['trajectory'].joint_positions[-1]);current=np.asarray(encoder)
        error=float(np.max(abs(current-end)))
        versions=snapshot.get('object_versions',{})
        changes={k:dict(recorded=v,current=versions.get(k)) for k,v in record['other_versions'].items() if versions.get(k)!=v}
        checks.update(object_matches=record['object']==obj,revision_matches=record['revision']==revision,
            gripper_closed=snapshot.get('last_gripper_command')=='closed',encoder_at_endpoint=error<.05,
            scene_versions_match=not changes)
        details=dict(recorded_object=record['object'],recorded_revision=record['revision'],current_revision=revision,
            endpoint_max_error_rad=error,endpoint_tolerance_rad=.05,trajectory_endpoint=end.tolist(),
            current_encoders=current.tolist(),changed_objects=changes,
            recorded_scene_versions=dict(record['other_versions']))
    ok=all(checks.values())
    return dict(status='verified' if ok else 'unknown',reason='recorded_reverse_path' if ok else 'recorded_restore_not_available',
        checks=checks,blockers=[k for k,v in checks.items() if not v],details=details,
        scope='same_goal_and_observed_scene; not_new_motion_certification')
