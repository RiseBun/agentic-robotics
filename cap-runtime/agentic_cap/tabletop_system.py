"""Dynamic plans and evidence. Simulator truth and model assertions are not inputs."""
import copy
import time
from .tabletop_domain import SKILLS, digest, fact_key, condition_from_key, effects_for, validate_plan
from .tabletop_scene import public_catalog, system_description
from .tabletop_recovery import context_for, constraint, operation


class TabletopSystem:
    def __init__(self,episode_id):
        self.episode_id=episode_id;self.goal_version=1;self.plan_version=0
        self.plan={'nodes':[]};self.latest=None;self.cuts={};self.catalog=public_catalog()

    def change_goal(self):
        self.goal_version+=1;self.plan_version+=1;self.plan={'nodes':[]};self.latest=None;self.cuts={}

    def set_plan(self,plan):
        self.plan=validate_plan(plan,self.catalog);self.plan_version+=1;self.cuts={}
        return copy.deepcopy(self.plan)

    def publish(self,snapshot,goal,revision,verify,*,status,active,segments,alert=None,hold_pending=False,assistance=None,budget=None):
        age=time.time()-snapshot.get('captured_at',0)
        fresh=0<=age<=1 and status=='ready' and not hold_pending
        source={k:snapshot.get(k) for k in ('evidence_ref','captured_at','control_step')}
        source.update(revision=revision,goal_version=self.goal_version)
        facts={};conditions={fact_key(c):c for c in goal['all']}
        conditions['empty_gripper']={'predicate':'empty_gripper'}
        for obj in self.catalog['objects']:
            for predicate in ['visible','geometry','holding','accessible','recorded_descent','restored_pose','support_geometry']:
                c=dict(predicate=predicate,object=obj);conditions[fact_key(c)]=c
        for obj,d in self.catalog['objects'].items():
            if not d.get('movable',False):continue
            for support in self.catalog['objects']:
                if obj!=support:
                    c=dict(predicate='on',object=obj,support=support);conditions[fact_key(c)]=c
        for region in self.catalog['regions']:
            conditions['region:'+region]=dict(predicate='region',region=region)
        for node in self.plan['nodes']:
            name=node['skill']['name'];args=node['skill']['arguments']
            for k in SKILLS[name]['requires']+SKILLS[name]['ensures']:
                key=k.format(**args);conditions[key]=condition_from_key(key)
        for key,c in conditions.items():
            try:result=copy.deepcopy(verify({'all':[c]},snapshot))
            except Exception:result=dict(status='unknown',reason='public_verifier_unavailable')
            if not fresh or result.get('status') not in {'verified','failed','unknown'}:
                result=dict(status='unknown',reason='stale_or_unavailable_public_evidence')
            facts[key]=dict(status=result['status'],result=result,source=copy.deepcopy(source))
        try:g=copy.deepcopy(verify(goal,snapshot))
        except Exception:g=dict(status='unknown',reason='public_verifier_unavailable')
        if not fresh or g.get('status') not in {'verified','failed','unknown'}:g=dict(status='unknown',reason='stale_or_unavailable_public_evidence')
        facts['goal']=dict(status=g['status'],result=g,source=copy.deepcopy(source))
        nodes=[];completed=set()
        for n in self.plan['nodes']:
            ident=f'{self.goal_version}:p{self.plan_version}_{n["id"]}'
            attempts=[s for s in segments.values() if s.get('task_binding',{}).get('node_id')==ident]
            if any(s.get('node_verification',{}).get('status')=='verified' for s in attempts):completed.add(n['id'])
        for n in self.plan['nodes']:
            name=n['skill']['name'];args=n['skill']['arguments']
            required=[dict(fact=k.format(**args),expected='verified') for k in SKILLS[name]['requires']]
            effects=[dict(fact=k.format(**args),expected='verified') for k in SKILLS[name]['ensures']]
            observed=all(facts[c['fact']]['status']==c['expected'] for c in effects)
            if name=='inspect_object':observed=False  # An explicit acquisition must produce a new result.
            deps=all(d in completed for d in n['depends_on'])
            ready=fresh and deps and all(facts[c['fact']]['status']==c['expected'] for c in required)
            state='completed' if n['id'] in completed else 'satisfied' if observed else 'ready' if ready else 'blocked'
            ident=f'{self.goal_version}:p{self.plan_version}_{n["id"]}'
            nodes.append(dict(id=ident,plan_node=n['id'],kind=name,title=n['title'],arguments=args,skills=[name],
                requires=required,requirements=required,postconditions=effects,depends_on=n['depends_on'],
                status=state,route='registered_skill',skill_only=False,
                lifecycle=dict(current_satisfied=observed,historical_verified=n['id'] in completed)))
        # A node can reuse an already established effect without replaying its action.
        satisfied={n['plan_node'] for n in nodes if n['status'] in {'completed','satisfied'}}
        for n in nodes:
            if n['status']=='blocked' and fresh and all(d in satisfied for d in n['depends_on']) and all(facts[c['fact']]['status']==c['expected'] for c in n['requirements']):n['status']='ready'
        for n in nodes:
            skill=operation(n)
            context=context_for(skill,self.goal_version,snapshot,segments)
            n['recovery_context_key']=digest(context)
            retry=constraint(skill,context,segments)
            if retry:
                n['retry_constraint']=retry
                if retry['blocked'] and n['status']=='ready':n['status']='blocked'
        versions=snapshot.get('object_versions',{})
        signature=dict(goal_version=self.goal_version,plan_version=self.plan_version,revision=revision,
                       facts={k:v['status'] for k,v in facts.items()},object_versions=versions,
                       recovery={n['id']:n.get('retry_constraint') for n in nodes})
        state_id=digest(signature)
        ready=[n['id'] for n in nodes if n['status']=='ready'] if not active and not assistance else []
        phase='running' if active else 'holding' if hold_pending else 'awaiting_user' if assistance else 'verified' if g['status']=='verified' else 'ready'
        current_attempts=[s for s in segments.values() if s.get('revision')==revision]
        last_feedback=(current_attempts[-1].get('feedback') or {}).get('category') if current_attempts else None
        limits=(budget or {}).get('limits',{});used=(budget or {}).get('used',{})
        resources=dict(remaining={k:max(0,v-used.get(k,0)) for k,v in limits.items()})
        result=dict(contract_version=2,state_id=state_id,episode_id=self.episode_id,goal_version=self.goal_version,revision=revision,
            description=system_description(),world=dict(fresh=fresh,age_seconds=age,source=source,facts=facts,object_versions=copy.deepcopy(versions)),
            task=dict(goal=goal,nodes=nodes,plan_version=self.plan_version,status='verified' if g['status']=='verified' else 'active',
                      execution=dict(phase=phase,ready_nodes=ready,last_feedback_category=last_feedback)),
            skills=[dict(name=name,**copy.deepcopy(spec)) for name,spec in SKILLS.items()],
            executor=dict(active_segment=active,hold_pending=hold_pending),resources=resources)
        self.latest=result;self.cuts[state_id]=copy.deepcopy(result)
        while len(self.cuts)>64:self.cuts.pop(next(iter(self.cuts)))
        return copy.deepcopy(result)

    def bind(self,state_id,node_id,revision):
        old=self.cuts.get(state_id);now=self.latest
        if not old or not now or old['revision']!=revision or old['goal_version']!=self.goal_version:
            raise ValueError('Unknown or superseded plan/evidence reference')
        node=next((n for n in now['task']['nodes'] if n['id']==node_id),None)
        before=next((n for n in old['task']['nodes'] if n['id']==node_id),None)
        if not node or not before:raise ValueError('Node is not in the current plan')
        for key,obj in node['arguments'].items():
            if key in {'object','support'} and old['world']['object_versions'].get(obj)!=now['world']['object_versions'].get(obj):
                raise ValueError('Relevant object evidence changed')
        for c in node['requirements']:
            if old['world']['facts'][c['fact']]['status']!=now['world']['facts'][c['fact']]['status']:
                raise ValueError('Relevant prerequisite changed')
        return copy.deepcopy(node)

    def view(self,revision,status,active=None,segment=None,assistance=None,**kwargs):
        if self.latest is None:return None
        result=copy.deepcopy(self.latest)
        if status!='ready' or result['revision']!=revision:
            result['world']['fresh']=False;result['task']['execution']['ready_nodes']=[]
            result['executor']['active_segment']=active
            result['task']['status']=status;result['task']['execution']['phase']=status
            for node in result['task']['nodes']:node['status']='unknown'
            for fact in result['world']['facts'].values():fact.update(status='unknown',result=dict(status='unknown',reason='episode_unavailable'))
        return result
