from pathlib import Path
import json,subprocess,time
r=Path('/home/li/projects/agentic-robotics');o=r/'research/generic_robotics_followup_20261124'
end=time.monotonic()+600
while not (o/'skill-validation-place-v1/independent-validation.json').exists():
 if time.monotonic()>end:raise TimeoutError('Independent candidate validation did not close')
 time.sleep(2)
node='/home/li/.local/share/agentic-robotics/tools/node/bin/node'
with (o/'legacy-alternate-driver.log').open('w') as log:
 p=subprocess.run([node,'runtime/scripts/native-robot-trial.mjs','--output',str(o/'legacy-alternate-restack'),'--task','cube_restack','--seed','12','--secondary-view','wrist','--alternate-recovery','on','--port','8121'],cwd=r,stdout=log,stderr=subprocess.STDOUT,timeout=360)
(o/'alternate-queue-result.json').write_text(json.dumps(dict(exit_code=p.returncode)))
