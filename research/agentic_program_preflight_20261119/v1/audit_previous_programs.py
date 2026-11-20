import hashlib
import json
import sys
from pathlib import Path
root=Path(__file__).resolve().parent
project=root.parents[2]
sys.path.insert(0,str(project/'cap-runtime'))
from agentic_cap.interpreter import Interpreter, BoundaryError
calls=[]
tools={name:lambda *a,**k:calls.append((a,k)) for name in ['open_gripper','close_gripper','goto_pose','home_pose',
    'get_object_pose','sample_grasp_pose','check_holding','get_execution_state','inspect_support','inspect_placement']}
datasets=[('agentic_holding_model_20261119/v2',['recoverable','occluded']),
          ('agentic_multiview_model_20261119/v1',['baseline','candidate'])]
rows=[]; episodes=0
for dataset,modes in datasets:
    for mode in modes:
        for file in sorted((project/'research'/dataset/(mode+'-records')).glob('*/episode.json')):
            state=json.loads(file.read_text())
            if state['status']!='closed':raise RuntimeError('Require closed records')
            episodes+=1
            for segment in state['segments'].values():
                row=dict(dataset=dataset,mode=mode,episode_id=state['id'],segment_id=segment['id'],
                         old_execution=segment['execution'],old_error=segment.get('error'))
                try:Interpreter(tools,lambda:None).validate(segment['code']);row['preflight']='accepted'
                except (BoundaryError,SyntaxError) as exc:row.update(preflight='rejected',error=str(exc))
                rows.append(row)
if calls:raise RuntimeError('Preflight called a tool')
output=dict(episodes=episodes,segments=len(rows),api_calls=len(calls),rows=rows,
    completed_programs_rejected=sum(r['old_execution']=='completed' and r['preflight']=='rejected' for r in rows),
    unbound_nz_programs_rejected=sum(r['old_error']=="'nz'" and r['preflight']=='rejected' for r in rows),
    interpreter_sha256=hashlib.sha256((project/'cap-runtime/agentic_cap/interpreter.py').read_bytes()).hexdigest(),
    scope='Closed-code static replay, not repeated model/physics performance')
(root/'previous-program-audit.json').write_text(json.dumps(output,indent=2))
print(json.dumps({k:v for k,v in output.items() if k!='rows'}))
if episodes!=10 or output['completed_programs_rejected']:raise SystemExit(1)
