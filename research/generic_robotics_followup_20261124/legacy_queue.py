import json,subprocess,time
from pathlib import Path
r=Path('/home/li/projects/agentic-robotics');o=r/'research/generic_robotics_followup_20261124'
end=time.monotonic()+7200
while not (o/'development-v1/summary.json').exists():
 if time.monotonic()>end:raise TimeoutError('Development batch did not finish; legacy probes not started')
 time.sleep(5)
node='/home/li/.local/share/agentic-robotics/tools/node/bin/node'
rows=[]
for task in ['cube_lifting','cube_stack','cube_restack']:
 folder=o/('legacy-model-'+task)
 with (o/'legacy-model-driver.log').open('a') as log:
  proc=subprocess.run([node,'runtime/scripts/native-robot-trial.mjs','--output',str(folder),'--task',task,'--seed','12','--port','8121'],cwd=r,stdout=log,stderr=subprocess.STDOUT,timeout=360)
 rows.append(dict(task=task,exit_code=proc.returncode,folder=str(folder)))
 (o/'legacy-model-execution.json').write_text(json.dumps(rows,indent=2)+'\n')
 if proc.returncode:raise RuntimeError('Legacy protocol failure; do not retry silently')
print(json.dumps(rows))
