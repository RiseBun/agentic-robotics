import json,subprocess
from pathlib import Path
r=Path('/home/li/projects/agentic-robotics');o=r/'research/generic_robotics_followup_20261124';rows=[]
for task in ['cube_lifting','cube_stack','cube_restack']:
 folder=o/('profile3-'+task)
 with (o/'profile3-driver.log').open('a') as log:
  p=subprocess.run(['/home/li/.local/share/agentic-robotics/tools/node/bin/node','runtime/scripts/native-robot-trial.mjs','--output',str(folder),'--task',task,'--seed','12','--secondary-view','wrist','--alternate-recovery','on','--port','8121'],cwd=r,stdout=log,stderr=subprocess.STDOUT,timeout=360)
 rows.append(dict(task=task,exit_code=p.returncode));(o/'profile3-execution.json').write_text(json.dumps(rows,indent=2))
 if p.returncode:raise RuntimeError('Profile protocol failure')
