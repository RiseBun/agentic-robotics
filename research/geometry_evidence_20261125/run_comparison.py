import json,os,subprocess,sys
from pathlib import Path
r=Path('/home/li/projects/agentic-robotics');o=Path(__file__).parent;sys.path.insert(0,str(r/'cap-runtime'))
from agentic_cap.tabletop_campaign import TASKS
from agentic_cap.tabletop_evaluation import score_closed_batch
schedule=json.loads((o/'protocol.json').read_text())['schedule'];rows=[];fixed_model=None;source=None
for index,case in enumerate(schedule):
 folder=o/f"{index:02d}-{case['task']}-{case['seed']}-{case['perception']}"
 with (o/'driver.log').open('a') as log:
  p=subprocess.run(['/home/li/.local/share/agentic-robotics/tools/node/bin/node','runtime/scripts/tabletop-model-trial.mjs','--output',str(folder),'--task',case['task'],'--seed',str(case['seed']),'--perception',case['perception']],cwd=r,stdout=log,stderr=subprocess.STDOUT,timeout=660)
 row=dict(case,index=index,exit_code=p.returncode,folder=str(folder));rows.append(row)
 if p.returncode:
  (o/'execution.json').write_text(json.dumps(rows,indent=2));raise RuntimeError('Protocol failure; no automatic replay')
 protocol=json.loads((folder/'protocol.json').read_text());result=json.loads((folder/'result.json').read_text())
 if fixed_model is None:fixed_model=protocol['configuration_fingerprint'];source=protocol['runtime']['source_digest']
 if fixed_model!=protocol['configuration_fingerprint'] or source!=protocol['runtime']['source_digest']:raise RuntimeError('Source/model changed')
 row.update(status=result['status'],used=result['episode']['used'],task_wall_seconds=result.get('task_wall_seconds'))
 (o/'execution.json').write_text(json.dumps(rows,indent=2)+'\n')
for row in rows:
 for p in (Path(row['folder'])/'physical').glob('*/episode.json'):
  if json.loads(p.read_text())['status']!='closed':raise ValueError('Close all tasks before scoring')
scores=[]
for row in rows:
 goal=next(t['goal'] for t in TASKS if t['id']==row['task'])
 scores.extend(dict(task=row['task'],perception=row['perception'],task_wall_seconds=row['task_wall_seconds'],**s) for s in score_closed_batch(Path(row['folder'])/'physical',goal))
(o/'independent-scores.json').write_text(json.dumps(scores,indent=2)+'\n')
print(json.dumps([dict(task=s['task'],seed=s['seed'],perception=s['perception'],physical=s.get('physical_verified'),public=s.get('public_verified'),false_completion=s.get('false_completion')) for s in scores]))
