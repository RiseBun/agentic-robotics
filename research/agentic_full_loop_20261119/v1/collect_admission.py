import json
from pathlib import Path
root=Path(__file__).resolve().parent
if not (root/'suite-complete.json').exists():raise RuntimeError('Model suite host not complete')
rows=[]
for variant in ['normal','active']:
    campaign=root/(variant+'-campaign')
    if not (campaign/'host-result.json').exists():raise RuntimeError('Model host not complete')
    plan=json.loads((campaign/'campaign.json').read_text())['plan']
    for trial in plan:
        row=json.loads((campaign/trial['trial_id']/'result.json').read_text())
        rows.append(dict(variant=variant,trial_id=trial['trial_id'],episode_id=row.get('episode_id'),outcome=row['outcome']))
if len(rows)!=4:raise RuntimeError('Unexpected admission count')
(root/'closed-admission.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
print(json.dumps(rows))
