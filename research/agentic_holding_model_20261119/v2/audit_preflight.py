"""Replay saved code through static validation only; no API or physics calls."""
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
rows=[]
for mode in ['recoverable','occluded']:
    for file in sorted((root/(mode+'-records')).glob('*/episode.json')):
        state=json.loads(file.read_text())
        if state['status']!='closed':raise RuntimeError('Require closed records')
        for segment in state['segments'].values():
            result=dict(mode=mode,segment_id=segment['id'],old_execution=segment['execution'],old_error=segment.get('error'))
            try:Interpreter(tools,lambda:None).validate(segment['code']);result['preflight']='accepted'
            except (BoundaryError,SyntaxError) as exc:result.update(preflight='rejected',error=str(exc))
            rows.append(result)
if calls:raise RuntimeError('Validation unexpectedly called a tool')
output=dict(segments=len(rows),api_calls=len(calls),trials=rows,
    formerly_completed_rejected=sum(r['old_execution']=='completed' and r['preflight']=='rejected' for r in rows),
    original_unbound_typos_rejected=sum(r['old_error']=="'nz'" and r['preflight']=='rejected' for r in rows),
    interpreter_sha256=hashlib.sha256((project/'cap-runtime/agentic_cap/interpreter.py').read_bytes()).hexdigest(),
    scope='Static replay of closed program text; no new model or physical trial')
(root/'preflight-replay.json').write_text(json.dumps(output,indent=2))
print(json.dumps({k:v for k,v in output.items() if k!='trials'}))
if output['formerly_completed_rejected']:raise SystemExit(1)
