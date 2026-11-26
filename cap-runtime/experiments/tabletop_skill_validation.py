"""Offline candidate validation on reserved 301/302 seeds.

Preparation uses registered abilities and public evidence. Scoring runs only
once both physical episodes have closed. Never accepts model success claims.
"""
import argparse
import copy
import json
import time
from pathlib import Path
from agentic_cap.core import Manager
from agentic_cap.tabletop_backend import TabletopBackend
from agentic_cap.tabletop_domain import digest,effects_for,SKILLS
from agentic_cap.tabletop_scene import runtime_contract
from agentic_cap.tabletop_evaluation import score_closed_batch
from agentic_cap.tabletop_memory import SkillLibrary


def wait(fn,seconds=60):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if fn():return
        time.sleep(.05)
    raise TimeoutError('Validation timed out')


def validate_candidate(library_root,ident,bindings,records):
    library=SkillLibrary(library_root);candidate=json.loads((Path(library_root)/(ident+'.json')).read_text())
    if candidate['state']!='proposed':raise ValueError('Expected a proposed candidate')
    if candidate.get('runtime_contract')!=runtime_contract():raise ValueError('Candidate runtime/verifier contract changed; regenerate and independently validate')
    variables={v['name'] for v in candidate['variables']}
    if set(bindings)!=variables:raise ValueError('Explicit bindings must cover all candidate variables')
    steps=[dict(name=s['name'],arguments={k:(bindings[v[1:]] if v.startswith('$') else v) for k,v in s['arguments'].items()}) for s in candidate['steps']]
    final={'all':effects_for(steps[-1]['name'],steps[-1]['arguments'])}
    records=Path(records);records.mkdir(parents=True,exist_ok=False)
    manager=Manager(records,lambda e:TabletopBackend(e,evaluation=True));rows=[]
    def run_skill(e,name,args,request):
        e.submit_plan(request+'-plan',e.revision,dict(nodes=[dict(id='candidate',skill=dict(name=name,arguments=args))]))
        wait(lambda:e.status!='ready' or e.observe()['system']['task']['nodes'][0]['status']=='ready',5)
        system=e.observe()['system'];node=system['task']['nodes'][0]
        sid=e.execute_skill(request,e.revision,system['state_id'],node['id'],dict(name=name,arguments=args))
        wait(lambda:e.active is None,100)
        s=e.segments[sid]
        if s['execution']!='completed' or s.get('node_verification',{}).get('status')!='verified':raise ValueError('Candidate/preparation effect not established: '+str(s.get('error')))
        return sid
    for seed in [301,302]:
        created=manager.create('validation-'+ident+'-'+str(seed),scene='tabletop_v1',seed=seed,goal=final)
        e=manager.episodes[created['id']];row=dict(seed=seed,episode_id=e.id,candidate_segments=[],preparation_segments=[])
        try:
            wait(lambda:e.status=='failed' or bool(e.snapshot),30)
            if e.status!='ready':raise ValueError('Scene initialization failed')
            wait(lambda:all(e.observe()['system']['world']['facts'].get('geometry:'+a.get('object',''),{}).get('status')=='verified' for a in [s['arguments'] for s in steps]),20)
            # Establish the candidate's declared physical context, not an
            # unrelated empty-gripper scene. Preparation remains separately
            # recorded and cannot count as a candidate success.
            initial=candidate.get('initial_holding')
            if initial:
                obj=bindings[initial[1:]]
                for attempt in range(3):
                    if e.verify({'all':[dict(predicate='holding',object=obj)]})['status']=='verified':break
                    try:
                        sid=run_skill(e,'acquire_object',dict(object=obj),f'context-{attempt}')
                        row['preparation_segments'].append(sid)
                    except ValueError:
                        time.sleep(.8)
                        if e.verify({'all':[dict(predicate='holding',object=obj)]})['status']=='verified':break
                        if e.verify({'all':[dict(predicate='empty_gripper')]})['status']!='verified':raise
                if e.verify({'all':[dict(predicate='holding',object=obj)]})['status']!='verified':raise ValueError('Independent candidate context could not be established')
            for index,step in enumerate(steps):
                args=step['arguments'];requires=SKILLS[step['name']]['requires']
                if 'holding:{object}' in requires:
                    held=e.verify({'all':[dict(predicate='holding',object=args['object'])]})
                    if held['status']!='verified':
                        sid=run_skill(e,'acquire_object',dict(object=args['object']),f'prepare-{index}')
                        row['preparation_segments'].append(sid)
                sid=run_skill(e,step['name'],args,f'candidate-{index}');row['candidate_segments'].append(sid)
            time.sleep(1.);row['public_final']=e.finish(e.revision)
        except Exception as exc:row['error']=str(exc)
        finally:
            if e.status=='ready':time.sleep(.8)
            e.close();row['closed']=e.status=='closed';row['used']=e.state()['used'];rows.append(row)
            (records/'execution.json').write_text(json.dumps(rows,indent=2)+'\n')
    scores={r['episode_id']:r for r in score_closed_batch(records)}
    reports=[]
    for row in rows:
        score=scores[row['episode_id']]
        report=dict(schema='closed-tabletop-skill-validation-v1',candidate_id=ident,candidate_digest=digest(candidate),
            seed=row['seed'],episode_id=row['episode_id'],closed=row['closed'],physical_verified=score.get('physical_verified',False),
            public_verified=score.get('public_verified',False),false_completion=score.get('false_completion',False),
            candidate_completed=len(row['candidate_segments'])==len(steps) and not row.get('error'),
            plan_changed=False,budget_changed=False,error=row.get('error'),execution=row)
        reports.append(report)
    (records/'independent-validation.json').write_text(json.dumps(reports,indent=2)+'\n')
    library.validate(ident,reports)
    # Verification and publication are intentionally separate offline operations.
    return reports


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--library',required=True,type=Path)
    parser.add_argument('--candidate',required=True);parser.add_argument('--bindings',required=True,help='JSON variable to object/region mapping')
    parser.add_argument('--records',required=True,type=Path);args=parser.parse_args()
    if len(args.candidate)!=64 or any(c not in '0123456789abcdef' for c in args.candidate):parser.error('Invalid candidate ID')
    try:reports=validate_candidate(args.library,args.candidate,json.loads(args.bindings),args.records)
    except ValueError as exc:print(json.dumps(dict(status='not_promoted',reason=str(exc))));raise SystemExit(1)
    print(json.dumps(dict(status='verified_not_published',episodes=[r['episode_id'] for r in reports])))

if __name__=='__main__':main()
