"""Copy finite JSON evidence only after every admitted physical episode closes."""
import json
import sys
import tarfile
from pathlib import Path
root = Path(sys.argv[1])
states = []
for name in ['baseline', 'candidate']:
    preparation = json.loads((root / (name+'-records') / 'preparation.json').read_text())
    folder = root / (name+'-records') / preparation['episode_id']
    state = json.loads((folder / 'episode.json').read_text())
    events = [json.loads(line) for line in (folder / 'events.jsonl').read_text().splitlines() if line]
    if state['status'] != 'closed' or events[-1]['kind'] != 'episode_closed':
        raise RuntimeError('All admitted episodes must close before exporting independent evidence')
    states.append(dict(condition=name, episode_id=state['id'], status=state['status']))
with tarfile.open(root / 'offline-json.tar.gz', 'w:gz') as archive:
    for name in ['baseline', 'candidate']:
        for file in sorted((root / (name+'-records')).rglob('*')):
            if file.is_file() and file.suffix in {'.json', '.jsonl'}:
                archive.add(file, arcname=file.relative_to(root))
print(json.dumps(states))
