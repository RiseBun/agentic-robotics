"""Offline, closed-only evidence/cost analysis for tabletop development or campaigns."""
import argparse
import csv
import json
from collections import Counter,defaultdict
from pathlib import Path
from agentic_cap.tabletop_campaign import TASKS
from agentic_cap.tabletop_evaluation import score_episode


def lines(path):
    return [json.loads(s) for s in path.read_text().splitlines() if s.strip()] if path.exists() else []


def analyze(root):
    root=Path(root);files=sorted(root.glob('*/physical/*/episode.json'))
    if not files:raise ValueError('No physical episodes')
    episodes=[(p,json.loads(p.read_text())) for p in files]
    if any(e['status']!='closed' or e['active_segment'] is not None for _,e in episodes):raise ValueError('Close every task before independent analysis')
    rows=[];versions=set()
    for file,episode in episodes:
        folder=file.parent;run=folder.parent.parent;protocol=json.loads((run/'protocol.json').read_text())
        versions.add((protocol['configuration_fingerprint'],protocol['runtime_fingerprint']))
        task=next(t for t in TASKS if t['id']==protocol['task'])
        score=score_episode(folder,task['goal']);events=lines(folder/'events.jsonl')
        result=json.loads((run/'result.json').read_text());work=[]
        for f in (run/'workbench').glob('*/workbench.jsonl'):work.extend(lines(f))
        attempts=sorted(episode['segments'].values(),key=lambda s:s.get('started_at',0))
        failed=[s for s in attempts if s.get('execution')=='failed']
        success_after_failure=0;seen_failure=False
        for s in attempts:
            if s.get('execution')=='failed':seen_failure=True
            elif seen_failure and s.get('node_verification',{}).get('status')=='verified':success_after_failure+=1
        tokens=Counter();model_ms=0.
        for e in work:
            if e.get('kind')=='model_response':
                usage=(e.get('response') or {}).get('usage') or {};model_ms+=e.get('elapsed_ms',0)
                for key in ['input_tokens','output_tokens','total_tokens']:
                    if isinstance(usage.get(key),(int,float)):tokens[key]+=usage[key]
        acquisitions=[e for e in events if e['kind']=='evidence_acquisition_finished']
        views=[e for e in events if e['kind']=='view_attempt'];seen=set();repeats=0
        for e in views:
            sig=(e.get('object'),e.get('camera'),tuple(round(x,3) for x in e.get('target_xyz',[])))
            repeats+=sig in seen;seen.add(sig)
        starts={e['call_id']:e for e in events if e['kind']=='api_started'}
        placement_segments={e.get('segment_id') for e in events if e['kind']=='api_started' and e.get('name')=='plan_place'}
        admitted={};unguarded_releases=0
        segments=episode['segments']
        for e in events:
            segment=e.get('segment_id')
            if e['kind']=='release_admission_checked':admitted[segment]=e.get('result',{}).get('status')
            if e['kind']=='api_result' and e.get('name')=='set_gripper':
                start=starts.get(e.get('call_id'),{});args=start.get('args',[])
                command=args[0] if args else start.get('kwargs',{}).get('command')
                if command=='open' and segment in placement_segments and admitted.get(segment)!='verified':unguarded_releases+=1
        row=dict(task=task['id'],seed=episode['seed'],condition=protocol['condition'],episode_id=episode['id'],
            outcome=result['status'],physical_verified=score.get('physical_verified'),public_verified=score.get('public_verified'),
            false_completion=score.get('false_completion'),goal_translation_matches=score.get('registered_goal_matches_expected'),
            final_public_status=(result.get('verification') or {}).get('status','unknown'),
            model_transport_errors=sum(e.get('kind')=='model_transport_error' for e in work),
            model_429_responses=sum(e.get('kind')=='model_transport_error' and e.get('status')==429 for e in work),
            rate_limit_wait_seconds=sum(e.get('wait_ms',0) for e in work if e.get('kind')=='model_rate_limit_wait')/1000,
            model_protocol_rejections=sum(e.get('kind')=='model_protocol_rejected' for e in work),
            failed_segments=len(failed),subsequent_verified_segments=success_after_failure,
            task_recovered_after_failure=bool(failed and score.get('physical_verified') and score.get('public_verified')),
            assistance_requests=sum(e['kind']=='task_assistance_requested' for e in events),
            actual_human_interventions=0,
            evidence_acquisitions=len(acquisitions),evidence_available=sum(e.get('status')=='available' for e in acquisitions),
            active_views=len(views),active_acquisitions_available=sum(e.get('views_used',0)>0 and e.get('status')=='available' for e in acquisitions),
            repeated_view_targets=repeats,unguarded_releases=unguarded_releases,
            decisions=episode['used']['decisions'],api_calls=episode['used']['calls'],motion_steps=episode['used']['motion_steps'],idle_steps=episode['used']['idle_steps'],
            runtime_wall_seconds=episode['used']['wall_seconds'],task_wall_seconds=result.get('task_wall_seconds'),model_response_seconds=model_ms/1000,
            input_tokens=tokens.get('input_tokens'),output_tokens=tokens.get('output_tokens'),total_tokens=tokens.get('total_tokens'),
            runtime_fingerprint=protocol['runtime_fingerprint'],model_fingerprint=protocol['configuration_fingerprint'])
        rows.append(row)
    if len(versions)!=1:raise ValueError('Mixed source/model versions: do not aggregate')
    groups=[]
    for condition in sorted({r['condition'] for r in rows}):
        group=[r for r in rows if r['condition']==condition]
        covered={r['task'] for r in group if r['physical_verified'] and r['public_verified'] and r['goal_translation_matches']}
        groups.append(dict(condition=condition,runs=len(group),physical_successes=sum(bool(r['physical_verified']) for r in group),
            public_successes=sum(bool(r['public_verified']) for r in group),unscored=sum(r['physical_verified'] is None for r in group),false_completions=sum(bool(r['false_completion']) for r in group),
            final_unknown=sum(r['final_public_status']=='unknown' for r in group),goal_translation_mismatches=sum(not r['goal_translation_matches'] for r in group),
            recovered_tasks=sum(r['task_recovered_after_failure'] for r in group),assistance_requests=sum(r['assistance_requests'] for r in group),
            C_minimum_satisfied=covered=={t['id'] for t in TASKS} and not any(r['false_completion'] or r['unguarded_releases'] or r['repeated_view_targets'] for r in group),
            missing_task_classes=sorted({t['id'] for t in TASKS}-covered)))
    body=dict(schema=1,scope='closed tasks only; descriptive results, not statistical significance',
        recovery_definition='A failed segment followed by later verified segments and independently completed final task; not causal attribution.',
        evidence_metric_definition='Available acquisitions and successful active-view acquisitions. Full unknown-to-available ratio is not inferred from missing before-status telemetry.',
        human_intervention_definition='These autonomous runners provide no answers after the initial goal; assistance requests count separately.',
        groups=groups,runs=rows)
    (root/'analysis.json').write_text(json.dumps(body,indent=2)+'\n')
    with (root/'metrics.csv').open('w') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    return body


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True,type=Path);args=p.parse_args()
    print(json.dumps(analyze(args.root)['groups']))

if __name__=='__main__':main()
