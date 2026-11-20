import json
import sys
import tarfile
from pathlib import Path

root=Path(sys.argv[1]).resolve()
preparation=json.loads((root/'repair-records/preparation.json').read_text())
known=root/'repair-records'/preparation['episode_id']
if {p.parent for p in (root/'repair-records').glob('*/episode.json')}!={known}:
    raise RuntimeError('Unexpected episode; no replay')
state=json.loads((known/'episode.json').read_text())
events=[json.loads(s) for s in (known/'events.jsonl').read_text().splitlines()]
if state['status']!='closed' or events[-1]['kind']!='episode_closed':
    raise RuntimeError('Close original episode before evaluator export')
with tarfile.open(root/'offline-json.tar.gz','w:gz') as archive:
    for file in sorted((root/'repair-records').rglob('*')):
        if file.is_file() and file.suffix in {'.json','.jsonl'}:
            archive.add(file,arcname=file.relative_to(root))
print(json.dumps(dict(episode_id=state['id'],status=state['status'])))
