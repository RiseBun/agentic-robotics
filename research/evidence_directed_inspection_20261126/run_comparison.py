from pathlib import Path
import subprocess,json,os,time,hashlib
p=Path('/home/li/projects/agentic-robotics');out=p/'research/evidence_directed_inspection_20261126';protocol=json.loads((out/'protocol.json').read_text());node='/home/li/.local/share/agentic-robotics/tools/node/bin/node';results=[]
for case in protocol['runs']:
 for path,digest in protocol['source_sha256'].items():assert hashlib.sha256((p/path).read_bytes()).hexdigest()==digest,'source changed'
 script='tabletop-model-trial.mjs' if case['dropout']=='none' else 'rgbd-occlusion-trial.mjs'
 cmd=[node,'runtime/scripts/'+script,'--output',str(out/case['name']),'--task',case['task'],'--seed',str(case['seed']),'--inspection',case['inspection']]
 if case['dropout']!='none':cmd+=['--dropout',case['dropout']]
 (out/'progress.json').write_text(json.dumps(dict(active=case['name'],completed=results),indent=2))
 with (out/(case['name']+'.log')).open('w') as log:
  try:r=subprocess.run(cmd,cwd=p,stdout=log,stderr=subprocess.STDOUT,timeout=780);entry=dict(case,exit_code=r.returncode)
  except subprocess.TimeoutExpired:entry=dict(case,infrastructure='driver_timeout',exit_code=None)
 results.append(entry);(out/'execution.json').write_text(json.dumps(results,indent=2))
 if entry['exit_code']!=0:break
(out/'progress.json').write_text(json.dumps(dict(active=None,completed=results),indent=2))
