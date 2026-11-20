"""Check closure of both controls and all previously admitted suite episodes."""
import json
import sys
import tarfile
from pathlib import Path

root=Path(sys.argv[1]).resolve()
controls=json.loads((root/'assistance-admission.json').read_text())
prior=json.loads((root/'closed-admission.json').read_text())
if len(controls)!=2 or {r['mode'] for r in controls}!={'stop','goal_change'}:raise RuntimeError('Require both controls')
known=set()
for row in prior:
    if row.get('episode_id'):known.add(root/(row['variant']+'-records')/row['episode_id'])
selected=[]
for row in controls:
    identity=row.get('episode_id')
    if not identity or len(identity)!=32 or any(c not in '0123456789abcdef' for c in identity):raise RuntimeError('Bad ID')
    directory=root/'normal-records'/identity
    known.add(directory);selected.append(directory)
actual={p.parent for variant in ['normal','active'] for p in (root/(variant+'-records')).glob('*/episode.json')}
if actual!=known:raise RuntimeError('Unaccounted scene; inspect, no replay')
for directory in known:
    state=json.loads((directory/'episode.json').read_text())
    events=[json.loads(line) for line in (directory/'events.jsonl').read_text().splitlines()]
    if state['status']!='closed' or not any(e['kind']=='episode_closed' for e in events):raise RuntimeError('Close every scene first')
with tarfile.open(root/'assistance-json.tar.gz','w:gz') as archive:
    for directory in selected:
        # This protocol needs public event/state evidence only, no evaluator truth.
        for name in ['episode.json','events.jsonl']:
            archive.add(directory/name,arcname=directory.name+'/'+name)
print(json.dumps(dict(closed_total=len(known),exported_controls=len(selected))))
