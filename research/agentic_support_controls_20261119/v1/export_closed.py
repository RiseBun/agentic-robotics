import json
import sys
import tarfile
from pathlib import Path
root=Path(sys.argv[1]).resolve()
rows=json.loads((root/'records/results.json').read_text())
if len(rows)!=2 or {r['mode'] for r in rows}!={'stop','goal_change'}:raise RuntimeError('Require both cases')
known={root/'records'/r['episode_id'] for r in rows}
if {p.parent for p in (root/'records').glob('*/episode.json')}!=known:raise RuntimeError('Unaccounted episode')
for directory in known:
    state=json.loads((directory/'episode.json').read_text())
    events=[json.loads(line) for line in (directory/'events.jsonl').read_text().splitlines()]
    if state['status']!='closed' or not any(e['kind']=='episode_closed' for e in events):raise RuntimeError('Close all cases first')
with tarfile.open(root/'offline-json.tar.gz','w:gz') as archive:
    for file in sorted((root/'records').rglob('*')):
        if file.is_file() and file.suffix in {'.json','.jsonl'}:archive.add(file,arcname=file.relative_to(root))
print(json.dumps(dict(closed_episodes=len(known))))
