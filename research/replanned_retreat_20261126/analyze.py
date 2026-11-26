from pathlib import Path
import json,sys,re,numpy as np
from scipy.spatial.transform import Rotation
p=Path('/home/li/projects/agentic-robotics');sys.path.insert(0,str(p/'cap-runtime'))
from agentic_cap.tabletop_evaluation import score_episode
from agentic_cap.tabletop_campaign import TASKS
out=p/'research/replanned_retreat_20261126';protocol=json.loads((out/'protocol.json').read_text());goal=next(t for t in TASKS if t['id']=='support')['goal']
assert len(json.loads((out/'execution.json').read_text()))==len(protocol['runs']),'incomplete batch'
for case in protocol['runs']:
 for f in (out/case['name']/'physical').glob('*/episode.json'):assert json.loads(f.read_text())['status']=='closed'
scores=[];audits=[]
for case in protocol['runs']:
 root=out/case['name'];ep=next((root/'physical').glob('*/episode.json')).parent;result=json.loads((root/'result.json').read_text())
 score=score_episode(ep,goal);events=[json.loads(l) for l in (ep/'events.jsonl').read_text().splitlines()]
 starts=[e for e in events if e['kind']=='replanned_retreat_started'];retreats=[]
 private=[json.loads(l) for l in (ep/'evaluation-private.jsonl').read_text().splitlines()]
 for start in starts:
  accepted=next((e for e in events if e['kind']=='replanned_retreat_plan_accepted' and e['segment_id']==start['segment_id']),None)
  end=next((e for e in events if e['kind']=='replanned_retreat_finished' and e['segment_id']==start['segment_id']),None)
  row=dict(segment_id=start['segment_id'],old_reverse_status=start['old_path_admission']['status'],old_blockers=start['old_path_admission'].get('blockers'),planning_accepted=accepted is not None,executed=end is not None,post_holding=end.get('result') if end else None,target=start['target'])
  if accepted and end:
   lo=int(accepted['evidence_ref'].split('-')[1].split('.')[0]);hi=int(end['result']['evidence_ref'].split('-')[1].split('.')[0]);window=[r for r in private if lo<=r['control_step']<=hi]
   offsets=[];distances=[]
   for sample in window:
    with np.load(ep/f"frame-{sample['control_step']:06d}.npz",allow_pickle=False) as z:
     q=z['tcp_wxyz'];offsets.append(Rotation.from_quat(q[[1,2,3,0]]).inv().apply(np.array(sample['objects']['block_a']['position'])-sample['tcp_xyz']))
    distances.append(np.linalg.norm(np.array(sample['objects']['block_a']['position'])-sample['tcp_xyz']))
   with np.load(ep/end['result']['evidence_ref'],allow_pickle=False) as z:final_tcp=z['tcp_xyz']
   row['offline_motion']=dict(samples=len(window),closed_gripper_all=all(r['gripper_command']<.5 for r in window),
    maximum_object_tcp_distance_m=float(max(distances)),maximum_relative_body_drift_m=float(np.max(np.linalg.norm(np.array(offsets)-np.mean(offsets,axis=0),axis=1))),
    tcp_height_gain_m=window[-1]['tcp_xyz'][2]-window[0]['tcp_xyz'][2],final_tcp_error_m=float(np.linalg.norm(final_tcp-start['target']['xyz'])),
    scope='post-closure kinematic custody proxy only; not contact or collision certification')
  row['later_acquisitions']=[{k:e.get(k) for k in ['id','object','status','views_used','reason']} for e in events if end and e['id']>end['id'] and e['kind']=='evidence_acquisition_finished']
  retreats.append(row)
 row=dict(case,outcome=result['status'],task_wall_seconds=result['task_wall_seconds'],replan_attempts=len(starts),
  replan_executions=sum(r['executed'] for r in retreats),replan_verified_holding=sum((r['post_holding'] or {}).get('status')=='verified' for r in retreats),**score)
 scores.append(row);audits.append(dict(run=case['name'],retreats=retreats,events=[e for e in events if e['kind'].startswith('replanned_retreat') or e['kind'] in ['recorded_descent_saved','release_admission_checked','evidence_acquisition_finished','api_error']]))
 print('RESULT',case['name'],row['physical_verified'],row['public_verified'],row['false_completion'],'replans',row['replan_attempts'],row['replan_executions'],row['replan_verified_holding'])
(out/'independent-scores.json').write_text(json.dumps(scores,indent=2));(out/'audit.json').write_text(json.dumps(audits,indent=2))
