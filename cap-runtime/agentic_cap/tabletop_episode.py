"""Generic task lifecycle, sharing the existing sole physics owner and interpreter."""
import copy
import os
from pathlib import Path
from .core import Episode, StaleRevision
from .tabletop_domain import LIMITS, SKILLS, validate_goal, validate_skill, compile_skill, digest
from .tabletop_scene import public_catalog, system_description
from .tabletop_system import TabletopSystem


class TabletopEpisode(Episode):
    default_limits=LIMITS

    def validate_goal(self,goal):return validate_goal(goal,public_catalog())
    def validate_skill(self,skill):return validate_skill(skill,public_catalog())
    def compile_skill(self,skill,system,snapshot):return compile_skill(skill,public_catalog())
    def skill_definition(self,name):return SKILLS[name]
    def make_robot_system(self):return TabletopSystem(self.id)
    def evidence_goals(self):return [self.goal]

    def _publish_scene_change(self):
        # Per-object versions are checked by bindings. Unrelated objects do not
        # invalidate the goal or every model decision on each control tick.
        pass

    @property
    def policy(self):return (self.creation or {}).get('payload',{}).get('policy','system')

    def describe(self):
        result=system_description();result['execution_policy']=self.policy
        if self.backend is not None and hasattr(self.backend,'capability_health'):
            result['providers']=self.backend.capability_health()
        result['skills']=[dict(name=n,**copy.deepcopy(s)) for n,s in SKILLS.items()]
        return result

    def experiences(self):
        from .tabletop_memory import SkillLibrary
        root=os.environ.get('TABLETOP_SKILL_LIBRARY')
        if not root or not Path(root).is_dir():return dict(enabled=False,library_fingerprint=None,items=[])
        library=SkillLibrary(root)
        return dict(enabled=True,library_fingerprint=library.fingerprint(),items=library.retrieve(self.observe()['system']))

    def _bind_task(self,revision,state_id,task_node):
        from .tabletop_recovery import RecoveryBlocked,operation
        # Enforce before TaskExecutive converts all blocked nodes to generic
        # stale dependencies. Both skill and bound-code paths pass this gate.
        current=self.observe()['system']
        node=next((n for n in current['task']['nodes'] if n['id']==task_node),None)
        retry=(node or {}).get('retry_constraint',{})
        if revision==self.revision and retry.get('blocked'):
            self.event('recovery_dispatch_blocked',node_id=task_node,retry_constraint=retry)
            raise RecoveryBlocked('Repeated operation requires changed evidence, a different strategy, or user assistance')
        snapshot,system,node,binding=super()._bind_task(revision,state_id,task_node)
        binding.update(recovery_context_key=node['recovery_context_key'],recovery_operation=operation(node))
        return snapshot,system,node,binding

    def submit_plan(self,request_id,revision,plan):
        if self.policy!='system':raise ValueError('Direct CaP does not accept a task graph')
        return self._accept_plan(request_id,revision,plan)

    def submit_operation(self,request_id,revision,skill):
        if self.policy!='direct_cap':raise ValueError('Operation envelopes require direct CaP mode')
        self.validate_skill(skill)
        with self.lock:
            key='operation-envelope:'+request_id
            fingerprint=digest(dict(revision=revision,skill=skill))
            if key in self.requests:
                old=self.requests[key]
                if old['digest']!=fingerprint:raise ValueError('Operation idempotency conflict')
                return copy.deepcopy(old['result'])
            result=self._accept_plan('operation:'+request_id,revision,dict(nodes=[dict(id='operation',skill=skill)]))
            # The returned reference is a fresh admission envelope, not an S2
            # task graph. Bound code still uses registered conditions/effects.
            system=self.observe()['system'];node=system['task']['nodes'][0]
            envelope=dict(result,task_ref=dict(state_id=system['state_id'],task_node=node['id']))
            self.requests[key]=dict(digest=fingerprint,result=copy.deepcopy(envelope));self._persist()
            return envelope

    def _accept_plan(self,request_id,revision,plan):
        if not isinstance(request_id,str) or not 1<=len(request_id)<=128:raise ValueError('Invalid request ID')
        fingerprint=digest(dict(revision=revision,plan=plan));key='plan:'+request_id
        with self.lock:
            if key in self.requests:
                old=self.requests[key]
                if old['digest']!=fingerprint:raise ValueError('Plan idempotency conflict')
                return copy.deepcopy(old['result'])
            if revision!=self.revision:raise StaleRevision('Superseded plan goal')
            if self.status!='ready' or self.active or self.assistance or self.hold_ack<self.hold_serial:
                raise ValueError('Wait for an idle, available, owner-confirmed episode')
            accepted=self.robot_system.set_plan(plan)
            result=dict(plan_version=self.robot_system.plan_version,goal_version=self.robot_system.goal_version,plan=accepted)
            self.requests[key]=dict(digest=fingerprint,result=result)
            self.event('plan_accepted',**result)
            self.observe();return result
