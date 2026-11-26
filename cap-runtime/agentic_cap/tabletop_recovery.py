"""Task-local retry admission, based on registered operations and actual evidence actions."""
import copy
from .tabletop_domain import digest

GUARDED={'acquire_object','place_object','place_in_region','replan_grip_pose'}
MAX_UNCHANGED_FAILURES=2

class RecoveryBlocked(ValueError):
    code='RECOVERY_BLOCKED'

def operation(node):
    return dict(name=node['skills'][0],arguments=copy.deepcopy(node['arguments']))

def context_for(skill,goal_version,snapshot,segments):
    name=skill['name'];args=skill['arguments'];obj=args['object']
    relevant={obj,args.get('support')}-{None}
    versions=snapshot.get('object_versions',{})
    # A held object's travel/retreat does not repair a placement failure.
    watched=set(versions)-{obj} if name=='replan_grip_pose' else relevant if name=='acquire_object' else relevant-{obj}
    observations={};grasp=None
    for segment in segments.values():
        binding=segment.get('task_binding',{})
        if binding.get('goal_version')!=goal_version or 'result_revision' not in segment:continue
        actual=binding.get('recovery_operation') or segment.get('skill') or {}
        a=actual.get('arguments',{})
        if actual.get('name')=='acquire_object' and a.get('object')==obj and segment.get('node_verification',{}).get('status')=='verified':grasp=segment['id']
        if actual.get('name')!='inspect_object' or a.get('object') not in relevant:continue
        for call in segment.get('api_attempts',[]):
            result=call.get('result') or {}
            if call.get('name')=='inspect_object' and call.get('status')=='completed' and result.get('status')=='available' and result.get('views_used',0)>0:
                observations[a['object']]=segment['id']
    return dict(goal_version=goal_version,operation=skill,object_versions={k:versions.get(k) for k in sorted(watched)},
                verified_grasp=grasp if name!='acquire_object' else None,active_evidence=observations)

def constraint(skill,context,segments):
    if skill['name'] not in GUARDED:return None
    key=digest(context);failed=[]
    for segment in segments.values():
        binding=segment.get('task_binding',{})
        if binding.get('recovery_context_key')!=key or 'result_revision' not in segment:continue
        if segment.get('execution')=='cancelled' or not segment.get('api_attempts'):continue
        if segment.get('node_verification',{}).get('status')!='verified':
            failed.append(segment)
    if not failed:return None
    last=failed[-1];bad=next((c for c in reversed(last.get('api_attempts',[])) if c.get('status')=='failed'),{})
    return dict(blocked=len(failed)>=MAX_UNCHANGED_FAILURES,context_key=key,
        failures=len(failed),maximum_unchanged_failures=MAX_UNCHANGED_FAILURES,
        failed_segments=[s['id'] for s in failed],last_failed_api=bad.get('name'),
        reason='unchanged_operation_failures',
        next_action='acquire_distinct_evidence_or_replan_or_ask_user',
        scope='goal-local semantic operation; node labels, code wording, fresh timestamps and retreat do not clear failures')
