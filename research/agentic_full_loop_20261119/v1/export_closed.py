"""All four original model cases must be closed before evaluator export."""
import json
import sys
import tarfile
from pathlib import Path

root=Path(sys.argv[1]).resolve()
admitted=json.loads((root/'closed-admission.json').read_text())
if len(admitted)!=4 or len({(r['variant'],r['trial_id']) for r in admitted})!=4:
    raise RuntimeError('Require all four original model admissions')
known=set()
for row in admitted:
    if row['variant'] not in {'normal','active'}: raise RuntimeError('Unexpected variant')
    identity=row.get('episode_id')
    if not identity:
        if row['outcome']!='infrastructure_error': raise RuntimeError('Missing episode identity')
        continue
    if len(identity)!=32 or any(c not in '0123456789abcdef' for c in identity):raise RuntimeError('Bad episode ID')
    directory=root/(row['variant']+'-records')/identity
    state=json.loads((directory/'episode.json').read_text())
    events=[json.loads(line) for line in (directory/'events.jsonl').read_text().splitlines()]
    if state['status']!='closed' or not any(e['kind']=='episode_closed' for e in events):raise RuntimeError('Close all cases before private export')
    known.add(directory)
for variant in ['normal','active']:
    if {p.parent for p in (root/(variant+'-records')).glob('*/episode.json')}-known:
        raise RuntimeError('Unaccounted episode; inspect, do not replay')
with tarfile.open(root/'offline-json.tar.gz','w:gz') as archive:
    for directory in sorted(known):
        for file in sorted(directory.glob('*')):
            if file.is_file() and file.suffix in {'.json','.jsonl'}:archive.add(file,arcname=file.relative_to(root))
print(json.dumps(dict(closed_episodes=len(known),planned_cases=4)))
