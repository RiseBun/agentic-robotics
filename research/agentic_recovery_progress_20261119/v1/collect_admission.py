import json
from pathlib import Path
root = Path(__file__).resolve().parent
host = root/'campaign'
if not (host/'host-result.json').is_file(): raise RuntimeError('Host not terminal')
schedule = json.loads((host/'paired-campaign.json').read_text())['schedule']
rows = []
for case in schedule:
    folder = host/case['pair_id']/case['variant']
    plan = json.loads((folder/'campaign.json').read_text())['plan']
    if len(plan) != 1: raise RuntimeError('Unexpected trial count')
    row = json.loads((folder/plan[0]['trial_id']/'result.json').read_text())
    if row.get('close_error') or not row.get('source_unchanged'): raise RuntimeError('Closure/source audit failed')
    rows.append(dict(case, episode_id=row.get('episode_id'), outcome=row['outcome']))
if len(rows) != 2: raise RuntimeError('Incomplete schedule')
(root/'closed-admission.json').write_text(json.dumps(rows, indent=2))
print(json.dumps(rows))
