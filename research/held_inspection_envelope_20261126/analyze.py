from pathlib import Path
import sys,json,itertools,statistics
import numpy as np
p=Path('/home/li/projects/agentic-robotics');sys.path.insert(0,str(p/'cap-runtime'))
from agentic_cap.tabletop_evaluation import score_episode
from agentic_cap.tabletop_campaign import TASKS
from agentic_cap.tabletop_views import signature
out=p/'research/held_inspection_envelope_20261126';protocol=json.loads((out/'protocol.json').read_text());goal=next(t for t in TASKS if t['id']=='support')['goal']
assert len(json.loads((out/'execution.json').read_text()))==len(protocol['runs']),'comparison incomplete'
for c in protocol['runs']:
 for f in (out/c['name']/'physical').glob('*/episode.json'):assert json.loads(f.read_text())['status']=='closed','open episode'
scores=[];audit=[];configurations=set();source_digests=set();prompts=set();models=set()
for c in protocol['runs']:
 root=out/c['name'];result=json.loads((root/'result.json').read_text());eps=list((root/'physical').glob('*/episode.json'))
 run_protocol=json.loads((root/'protocol.json').read_text());configurations.add(run_protocol['configuration_fingerprint']);source_digests.add(run_protocol['runtime']['source_digest']);prompts.add(run_protocol['input_text']);models.add(run_protocol['model'])
 assert len(eps)==1,'expected one controlled physical episode'
 ep=eps[0].parent;score=score_episode(ep,goal);assert score.get('registered_goal_matches_expected'),score
 events=[json.loads(l) for l in (ep/'events.jsonl').read_text().splitlines()]
 requests=[e for e in events if e['kind']=='inspection_evidence_request'];orderings=[]
 for e in requests:
  rank=next((x for x in events if x['kind']=='inspection_candidates_ranked' and x['segment_id']==e['segment_id'] and x['id']>e['id']),None)
  assert rank is not None
  anchor=np.array(rank['anchor_xyz']);held=any(x['predicate']=='holding' for x in e['request']['requirements'])
  offsets=([np.array([0,dy,0]) for dy in [-.08,.08]] if held and not c['expanded'] else [np.array([dx,dy,dz]) for dz in [0,.06] for dx,dy in itertools.product([-.08,0,.08],repeat=2) if dx or dy or dz])
  original_order={signature(anchor+d):i for i,d in enumerate(offsets)}
  candidates=e['candidates'];baseline=sorted(candidates,key=lambda x:(-x['score'],original_order[tuple(x['signature'])]))
  before=[x['signature'] for x in baseline];after=[x['signature'] for x in candidates]
  assert sorted(before)==sorted(after)
  predictions=[x.get('evidence_prediction',{}) for x in candidates]
  orderings.append(dict(event_id=e['id'],request=e['request'],coverage_order=before,evidence_order=after,
   top_changed=before[:1]!=after[:1],first_two_changed=before[:2]!=after[:2],
   prediction_statuses=[x.get('status') for x in predictions],predictions=predictions))
 expanded_attempts=[]
 for event in events:
  if event['kind']!='view_attempt':continue
  previous=[x for x in events if x['kind']=='inspection_candidates_ranked' and x['segment_id']==event['segment_id'] and x['id']<event['id']]
  if not previous:continue
  anchor=np.asarray(previous[-1]['anchor_xyz']);delta=np.asarray(event['target_xyz'])-anchor
  assert abs(delta[0])<=.080001 and abs(delta[1])<=.080001 and any(abs(delta[2]-z)<1e-6 for z in [0,.06])
  if abs(delta[0])>.001 or abs(delta[2])>.001:expanded_attempts.append(dict(event_id=event['id'],delta_xyz=delta.tolist(),target_xyz=event['target_xyz']))
 acquisitions=[{k:e.get(k) for k in ['id','segment_id','object','status','reason','views_used','motion_steps','wall_seconds']} for e in events if e['kind']=='evidence_acquisition_finished']
 assert all(x['views_used']<=2 for x in acquisitions)
 usage=[];responses=0
 for f in (root/'workbench').glob('*/workbench.jsonl'):
  for line in f.read_text().splitlines():
   e=json.loads(line)
   if e.get('kind')=='model_response':
    responses+=1
    if isinstance(e.get('response',{}).get('usage'),dict):usage.append(e['response']['usage'])
 totals={k:sum(x.get(k,0) or 0 for x in usage) for k in ['input_tokens','output_tokens','total_tokens']}
 row=dict(c,outcome=result['status'],task_wall_seconds=result['task_wall_seconds'],
  model_responses=responses,usage_records=len(usage),model_usage=totals,
  fault_injected=any(e['kind']=='rgbd_dropout_started' for e in events),
  active_view_attempts=sum(e['kind']=='view_attempt' for e in events),
  completed_view_checks=sum(e['kind']=='inspection_post_view_evidence' for e in events),
  rejected_view_plans=sum(e['kind']=='inspection_candidate_rejected' for e in events),
  joint_support_holding_confirmations=sum(e['kind']=='inspection_post_view_evidence' and e.get('joint_result',{}).get('status')=='verified' and e.get('object')=='platform' and e.get('custody') is not None for e in events),
  expanded_pose_attempts=len(expanded_attempts),
  active_support_confirmations=sum(a['object']=='platform' and a['status']=='available' and a['views_used']>0 for a in acquisitions),
  dispatch_blocks=sum(e['kind']=='inspection_dispatch_blocked' for e in events),
  evidence_requests=len(requests),top_order_changes=sum(x['top_changed'] for x in orderings),
  unconfirmed_physical_completion=score['physical_verified'] and not score['public_verified'],**score)
 scores.append(row)
 audit.append(dict(run=c['name'],expanded_attempts=expanded_attempts,orderings=orderings,acquisitions=acquisitions,
  replan_events=[e for e in events if e['kind'].startswith('replanned_retreat')],
  dispatch_blocks=[e for e in events if e['kind']=='inspection_dispatch_blocked'],
  post_view_evidence=[e for e in events if e['kind']=='inspection_post_view_evidence'],
  releases=[e for e in events if e['kind']=='release_admission_checked'],
  terminal_events=[e for e in events if e['kind'] in ['task_assistance_requested','finish_checked']]))
 print('SCORE',c['name'],row['physical_verified'],row['public_verified'],row['false_completion'],row['active_view_attempts'],row['top_order_changes'])
assert len(configurations)==len(source_digests)==len(prompts)==len(models)==1,'comparison inputs changed'
(out/'comparison-integrity.json').write_text(json.dumps(dict(configuration_fingerprints=sorted(configurations),runtime_source_digests=sorted(source_digests),input_texts=sorted(prompts),models=sorted(models),all_episodes_closed=True),indent=2))
(out/'independent-scores.json').write_text(json.dumps(scores,indent=2));(out/'audit.json').write_text(json.dumps(audit,indent=2))
groups=[]
for mode in ['coverage','evidence']:
 rows=[r for r in scores if r['inspection']==mode and r['fault']]
 groups.append(dict(inspection=mode,runs=len(rows),physical_successes=sum(r['physical_verified'] for r in rows),public_successes=sum(r['public_verified'] for r in rows),false_completions=sum(r['false_completion'] for r in rows),
  unconfirmed_physical_completions=sum(r['unconfirmed_physical_completion'] for r in rows),
  total_active_view_attempts=sum(r['active_view_attempts'] for r in rows),
  mean_task_wall_seconds=statistics.mean(r['task_wall_seconds'] for r in rows),
  mean_motion_steps=statistics.mean(r['used']['motion_steps'] for r in rows),
  mean_total_tokens=statistics.mean(r['model_usage']['total_tokens'] for r in rows)))
(out/'summary.json').write_text(json.dumps(groups,indent=2));print('SUMMARY',json.dumps(groups))
