from pathlib import Path
import subprocess,json,time,hashlib
p=Path('/home/li/projects/agentic-robotics');out=p/'research/replanned_retreat_20261126';protocol=json.loads((out/'protocol.json').read_text());done=[]
while not (out/'checks.json').exists():time.sleep(2)
while set(json.loads((out/'checks.json').read_text()))!={'python','node'}:time.sleep(2)
assert all(x['exit_code']==0 for x in json.loads((out/'checks.json').read_text()).values()),'checks failed'
for case in protocol['runs']:
 for name,digest in protocol['source_sha256'].items():assert hashlib.sha256((p/name).read_bytes()).hexdigest()==digest,'source changed'
 (out/'progress.json').write_text(json.dumps(dict(active=case['name'],completed=done),indent=2))
 script='rgbd-occlusion-trial.mjs' if case['fault'] else 'tabletop-model-trial.mjs'
 cmd=['/home/li/.local/share/agentic-robotics/tools/node/bin/node','runtime/scripts/'+script,'--output',str(out/case['name']),'--task','support','--seed',str(case['seed']),'--inspection','evidence']
 if case['fault']:cmd+=['--dropout','partial']
 if case['replanned_retreat']:cmd+=['--replanned-retreat']
 with (out/(case['name']+'.log')).open('w') as log:r=subprocess.run(cmd,cwd=p,stdout=log,stderr=subprocess.STDOUT,timeout=780)
 done.append(dict(case,exit_code=r.returncode));(out/'execution.json').write_text(json.dumps(done,indent=2))
 if r.returncode:break
(out/'progress.json').write_text(json.dumps(dict(active=None,completed=done),indent=2))
