from pathlib import Path
import subprocess,os,json
r=Path('/home/li/projects/agentic-robotics');o=r/'research/generic_robotics_followup_20261124'
node='/home/li/.local/share/agentic-robotics/tools/node/bin/node';env=os.environ.copy();env.update(CAP_ACTIVE_EVIDENCE='1',CAP_PLACEMENT_EVIDENCE='1',CAP_HOLDING_WATCHDOG='1',CAP_ALTERNATE_PLACEMENT='1',CAP_SUPPORT_SECOND_VIEW='robot0_eye_in_hand')
commands=[('fault-current',[node,'runtime/scripts/tabletop-fault-probe.mjs',str(o/'fault-current')]),('smoke-current',[node,'runtime/scripts/tabletop-model-trial.mjs','--output',str(o/'smoke-current'),'--task','multiple','--seed','202'])]
for name,command in commands:
 with (o/(name+'-driver.log')).open('w') as log:p=subprocess.run(command,cwd=r,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=660)
 (o/(name+'-exit.json')).write_text(json.dumps(dict(exit_code=p.returncode)))
 if p.returncode:raise RuntimeError('Preflight protocol failure: '+name)
