import json
import sys
import tarfile
from pathlib import Path

root=Path(sys.argv[1]).resolve()
admitted=json.loads((root/'closed-admission.json').read_text())
if len(admitted)!=1 or len({r['trial_id'] for r in admitted})!=1:
    raise RuntimeError('Require one original trial')
known=set()
for row in admitted:
    identity=row.get('episode_id')
    if not identity:
        if row['outcome']!='infrastructure_error':
            raise RuntimeError('Missing episode identity')
        continue
    if len(identity)!=32 or any(c not in '0123456789abcdef' for c in identity):
        raise RuntimeError('Bad identity')
    directory=root/'normal-records'/identity
    state=json.loads((directory/'episode.json').read_text())
    events=[json.loads(s) for s in (directory/'events.jsonl').read_text().splitlines()]
    if state['status']!='closed' or events[-1]['kind']!='episode_closed':
        raise RuntimeError('Close every episode before private export')
    known.add(directory)
if {p.parent for p in (root/'normal-records').glob('*/episode.json')}!=known:
    raise RuntimeError('Unaccounted episode')
with tarfile.open(root/'offline-json.tar.gz','w:gz') as archive:
    for directory in sorted(known):
        for file in sorted(directory.glob('*')):
            if file.is_file() and file.suffix in {'.json','.jsonl'}:
                archive.add(file,arcname=file.relative_to(root))
print(json.dumps(dict(closed_episodes=len(known))))
