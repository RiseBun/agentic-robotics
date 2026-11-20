import json
from pathlib import Path

root=Path(__file__).resolve().parent
host_result=root/'normal-campaign/host-result.json'
host_error=root/'normal-campaign/host-error.json'
if not host_result.exists() and not host_error.exists():
    raise RuntimeError('Original natural host must be terminal first; a live host cannot be replaced')
plan=json.loads((root/'normal-campaign/campaign.json').read_text())['plan']
rows=[]
for trial in plan:
    result=json.loads((root/'normal-campaign'/trial['trial_id']/'result.json').read_text())
    rows.append(dict(variant='normal',trial_id=trial['trial_id'],episode_id=result.get('episode_id'),outcome=result['outcome']))
if len(rows)!=1:
    raise RuntimeError('Require the one original natural admission')
(root/'closed-admission.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
(root/'host-terminal-audit.json').write_text(json.dumps(dict(
    status='failed' if host_error.exists() else 'completed',
    host_error=json.loads(host_error.read_text()) if host_error.exists() else None,
    note='Every original trial has a terminal result; authoritative closure is separately checked before export'),indent=2),encoding='utf-8')
print(json.dumps(rows))
