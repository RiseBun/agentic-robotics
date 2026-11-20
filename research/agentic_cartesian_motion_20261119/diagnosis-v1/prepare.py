"""Derive IK replay inputs from recorded public model/Runtime observations."""
import hashlib
import json
from datetime import datetime
from pathlib import Path

root = Path(__file__).resolve().parent
previous = root.parents[1] / 'agentic_full_loop_20261119/v1'
events_file = previous / 'normal-records/fa28381fd01fa12fb4ed05a3e2f1b858/events.jsonl'
workbench_file = next((previous / 'normal-campaign/cube_stack-s73-r0/records').glob('*/workbench.jsonl'))
events = [json.loads(s) for s in events_file.read_text().splitlines()]
workbench = [json.loads(s) for s in workbench_file.read_text().splitlines()]
observations = []
for row in workbench:
    if row['kind'] != 'model_request':
        continue
    for msg in row['body'].get('input', []):
        for content in msg.get('content', []):
            if content.get('type') == 'input_text':
                data = json.loads(content['text'])
                if data.get('latest_observation'):
                    observations.append((datetime.fromisoformat(row['time'].replace('Z', '+00:00')).timestamp(),
                                         data['latest_observation']))
rows = []
for event_id in [10, 112]:
    event = next(e for e in events if e['id'] == event_id)
    assert event['kind'] == 'api_started' and event['name'] == 'goto_pose'
    start_time, before = max((t, s) for t, s in observations if t < event['time'])
    after = next(e['result'] for e in events if e['id'] > event_id and e['kind'] == 'api_result'
                 and e.get('name') == 'get_execution_state')
    rows.append(dict(event_id=event_id, target_xyz=event['args'][0], target_wxyz=event['args'][1],
        initial_joints=before['joint_positions'], initial_evidence_ref=before['evidence_ref'],
        initial_control_step=before['control_step'], observation_to_request_seconds=event['time']-start_time,
        historical_measured_xyz=after['tcp_xyz'], historical_measured_wxyz=after['tcp_wxyz'],
        historical_measured_joints=after['joint_positions'], historical_motion=after['last_motion']))
result = dict(cases=rows, protocol=dict(refinement_attempts=5,
    variants=['single_velocity_cost', 'repeated_velocity_cost', 'pose_only'],
    scope='Public-record IK residual diagnosis, no environment, no physical completion inference'),
    inputs_sha256={str(p.relative_to(previous)): hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in [events_file, workbench_file]})
(root / 'inputs.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(dict(cases=len(rows))))
