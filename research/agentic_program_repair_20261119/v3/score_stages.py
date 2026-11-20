"""Closed-only contact evidence for repaired phases; never imported online."""
import json
from pathlib import Path

root=Path(__file__).resolve().parent
preparation=json.loads((root/'repair-records/preparation.json').read_text())
directory=root/'repair-records'/preparation['episode_id']
state=json.loads((directory/'episode.json').read_text())
events=[json.loads(s) for s in (directory/'events.jsonl').read_text().splitlines()]
if state['status']!='closed' or events[-1]['kind']!='episode_closed':
    raise RuntimeError('Close before independent evaluation')
truth=[json.loads(s) for s in (directory/'evaluation-private.jsonl').read_text().splitlines()]
handoff=next(e['id'] for e in events if e['kind']=='diagnostic_model_handoff_ready')
rows=[]
for name in ['green cube','red cube']:
    held=next((e for e in events if e['id']>handoff and e['kind']=='api_result'
        and e.get('name')=='check_holding' and e.get('result',{}).get('status')=='verified'
        and e['result'].get('evidence',{}).get('object_name')==name),None)
    release=next((e for e in events if held and e['id']>held['id']
        and e['kind']=='api_started' and e.get('name')=='open_gripper'),None)
    end=release['time'] if release else events[-1]['time']
    samples=[s for s in truth if held and held['time']<=s['time']<end]
    contacts=[s.get('bilateral_contact',{}).get(name) for s in samples]
    span=samples[-1]['time']-samples[0]['time'] if samples else 0
    status=('unknown' if len(samples)<2 or span<.5 else 'failed' if any(c is False for c in contacts)
        else 'verified' if all(c is True for c in contacts) else 'unknown')
    rows.append(dict(object_name=name,public_holding_verified=bool(held),contact_proxy=status,
        samples=len(samples),span_seconds=span,window_end='release_request' if release else 'episode_closed'))
output=dict(rows=rows,scope='Bilateral finger contact after public holding checks until next release request or close; not full-task completion')
(root/'stage-contact-score.json').write_text(json.dumps(output,indent=2))
print(json.dumps(output))
