"""Versioned external skills. Promotion consumes independent closed-task evidence.

No online evaluator truth, devices or model credentials are accessed here.
"""
import copy
import json
from pathlib import Path
from .tabletop_domain import digest, SKILLS


def parameterize_trace(trace):
    episode=trace.get('episode',{});events=trace.get('events',[])
    if episode.get('status')!='closed' or episode.get('task')!='tabletop' or episode.get('seed') not in {201,202}:raise ValueError('A closed tabletop public trace is required')
    steps=[];variables={};failures=[]
    for event in events:
        if event.get('kind') in {'api_error','verification_error'}:
            failures.append(copy.deepcopy(event));steps=[];variables={}
            continue
        if event.get('kind')!='segment_finished' or not failures:continue
        segment=event.get('segment',{});skill=segment.get('skill')
        if not skill or segment.get('execution')!='completed' or segment.get('node_verification',{}).get('status')!='verified':continue
        name=skill['name']
        if name not in SKILLS:raise ValueError('Trace contains an unregistered skill')
        args={}
        for k,value in skill['arguments'].items():
            if k in SKILLS[name].get('optional_parameters',{}):
                args[k]=value;continue
            identity=(k=='region',value)
            if identity not in variables:variables[identity]=f'arg{len(variables)+1}'
            args[k]='$'+variables[identity]
        steps.append(dict(name=name,arguments=args))
    if not failures or not steps:raise ValueError('A repair candidate requires failure evidence followed by verified steps')
    initial_holding=None;acquired=set()
    for step in steps:
        name=step['name'];args=step['arguments']
        needs_hold='holding:{object}' in SKILLS[name]['requires'] or name=='restore_grip_pose' or (name=='inspect_object' and args.get('evidence')=='holding')
        if needs_hold and args['object'] not in acquired:
            initial_holding=args['object'];break
        if name=='acquire_object':acquired.add(args['object'])
    goal_predicates=sorted({c['predicate'] for c in episode.get('goal',{}).get('all',[])})
    body=dict(initial_holding=initial_holding,goal_predicates=goal_predicates,schema=1,state='proposed',source_episode=episode['id'],source_seed=episode['seed'],
        variables=[dict(name=v,type='region' if k[0] else 'object') for k,v in variables.items()],
        runtime_contract=episode.get('system',{}).get('description',{}).get('runtime_contract'),
        steps=steps,requires=dict(skills=sorted({s['name'] for s in steps}),frame='world',
            predicates='Runtime registered prerequisites; fresh public evidence required',
            trigger_categories=sorted({(e.get('feedback') or {}).get('category','api_exception') for e in failures})),
        failure_evidence=[dict(kind=e['kind'],event_id=e['id'],api=e.get('name'),message=e.get('error'),category=(e.get('feedback') or {}).get('category'),evidence_ref=((e.get('feedback') or {}).get('observation') or {}).get('evidence_ref')) for e in failures],validation=[])
    body['id']=digest(body);return body


class SkillLibrary:
    def __init__(self,root):self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
    def _files(self):return sorted(self.root.glob('*.json'))
    def fingerprint(self):return digest([json.loads(f.read_text()) for f in self._files()])
    def save_candidate(self,candidate):
        body={k:v for k,v in candidate.items() if k!='id'}
        if candidate.get('id')!=digest(body) or candidate.get('state')!='proposed':raise ValueError('Invalid candidate digest/state')
        variables={v['name'] for v in candidate.get('variables',[])}
        for step in candidate.get('steps',[]):
            if step.get('name') not in SKILLS:raise ValueError('Unregistered candidate step')
            spec=SKILLS[step['name']];optional=spec.get('optional_parameters',{});args=step.get('arguments',{})
            if not set(spec['parameters'])<=set(args) or set(args)-set(spec['parameters'])-set(optional):raise ValueError('Invalid candidate parameters')
            for key,value in args.items():
                if key in optional:
                    if value not in optional[key]:raise ValueError('Invalid candidate evidence target')
                elif not isinstance(value,str) or not value.startswith('$') or value[1:] not in variables:
                    raise ValueError('Candidate contains fixed object names or coordinates')
        file=self.root/(candidate['id']+'.json')
        if file.exists():
            if json.loads(file.read_text())!=candidate:raise ValueError('Candidate ID conflict')
            return candidate['id']
        file.write_text(json.dumps(candidate,indent=2)+'\n');return candidate['id']
    def validate(self,ident,reports):
        if not isinstance(ident,str) or len(ident)!=64 or any(c not in '0123456789abcdef' for c in ident):raise ValueError('Invalid candidate ID')
        file=self.root/(ident+'.json');candidate=json.loads(file.read_text())
        if candidate['state']!='proposed':raise ValueError('Candidate is not proposed')
        # Reports must be produced by the offline validator, not S2 claims.
        seeds=set();episodes=set()
        for r in reports:
            if r.get('schema')!='closed-tabletop-skill-validation-v1' or r.get('candidate_id')!=ident or r.get('candidate_digest')!=digest(candidate):raise ValueError('Invalid validation report binding')
            if not r.get('closed') or not r.get('candidate_completed') or r.get('error') or not r.get('physical_verified') or not r.get('public_verified') or r.get('false_completion'):
                raise ValueError('Candidate did not pass independent physical and public validation')
            if r['seed'] not in {301,302} or r['episode_id']==candidate['source_episode']:raise ValueError('Validation must use independent reserved scenes')
            if r.get('plan_changed') or r.get('budget_changed'):raise ValueError('Validation protocol changed')
            seeds.add(r['seed']);episodes.add(r['episode_id'])
        if seeds!={301,302} or len(episodes)<2:raise ValueError('Both independent validation seeds are required')
        candidate.update(state='verified',validation=copy.deepcopy(reports));file.write_text(json.dumps(candidate,indent=2)+'\n')
    def publish(self,ident):
        if not isinstance(ident,str) or len(ident)!=64 or any(c not in '0123456789abcdef' for c in ident):raise ValueError('Invalid candidate ID')
        file=self.root/(ident+'.json');candidate=json.loads(file.read_text())
        if candidate['state']!='verified':raise ValueError('Only independently verified skills may be published')
        candidate['state']='library';file.write_text(json.dumps(candidate,indent=2)+'\n')
    def retrieve(self,system,*,limit=3):
        if not system.get('world',{}).get('fresh'):return []
        available={s['name'] for s in system.get('skills',[])};result=[]
        trigger=system.get('task',{}).get('execution',{}).get('last_feedback_category')
        frame=system.get('description',{}).get('robot',{}).get('frame')
        for file in self._files():
            c=json.loads(file.read_text())
            if c.get('state')!='library' or not set(c['requires']['skills'])<=available:continue
            if frame!=c['requires']['frame'] or trigger not in c['requires'].get('trigger_categories',[]):continue
            if not c.get('runtime_contract') or c['runtime_contract']!=system.get('description',{}).get('runtime_contract'):continue
            if c.get('source_episode')==system.get('episode_id'):continue
            current_predicates={row['predicate'] for row in system.get('task',{}).get('goal',{}).get('all',[])}
            if not set(c.get('goal_predicates',[]))<=current_predicates:continue
            result.append(dict(c,usage='Historical parameterized proposal only; rebind and recheck all Runtime conditions'))
        return result[:limit]
