import json
from pathlib import Path

root=Path(__file__).resolve().parent
if not (root/'suite-complete.json').exists():
    raise RuntimeError('Host must finish; do not replay')
campaign=root/'normal-campaign'
if not (campaign/'host-result.json').exists():
    raise RuntimeError('Host not complete')
plan=json.loads((campaign/'campaign.json').read_text())['plan']
rows=[]
for trial in plan:
    row=json.loads((campaign/trial['trial_id']/'result.json').read_text())
    rows.append(dict(variant='normal',trial_id=trial['trial_id'],episode_id=row.get('episode_id'),outcome=row['outcome']))
if len(rows)!=3:
    raise RuntimeError('Require three original admissions')
(root/'closed-admission.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
print(json.dumps(rows))
