"""Public-only target/reached audit; it does not identify object-pose truth."""
import json
import math
from pathlib import Path
root=Path(__file__).resolve().parent
directory=root/'normal-records/fa28381fd01fa12fb4ed05a3e2f1b858'
events=[json.loads(line) for line in (directory/'events.jsonl').read_text().splitlines()]
state=json.loads((directory/'episode.json').read_text())
if state['status']!='closed':raise RuntimeError('Closed-only analysis')
rows=[]
target=None
for event in events:
    if event['kind']=='api_started' and event.get('name')=='goto_pose':
        args=event.get('args',[])
        if args and isinstance(args[0],list) and len(args[0])==3:
            target=dict(event_id=event['id'],segment_id=event.get('segment_id'),target_xyz=args[0])
    if event['kind']=='api_started' and event.get('name')=='home_pose':target=None
    if event['kind']=='api_result' and event.get('name')=='get_execution_state' and target:
        result=event['result']
        xyz=result.get('tcp_xyz')
        if xyz and event.get('segment_id')==target['segment_id']:
            rows.append(dict(target,measured_xyz=xyz,error_m=math.dist(xyz,target['target_xyz']),
                last_motion=result.get('last_motion'),evidence_ref=result.get('evidence_ref'),
                subgoal=state['segments'][target['segment_id']]['subgoal']))
        target=None
(root/'public-motion-audit.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(rows,ensure_ascii=False))
