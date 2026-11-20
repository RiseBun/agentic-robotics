import json
import sys
import tarfile
from pathlib import Path
root = Path(sys.argv[1]).resolve()
records = root/'records'
rows = json.loads((records/'results.json').read_text())
if [r['mode'] for r in rows] != ['no_fault', 'actuator_release', 'red_pixels_missing']:
    raise RuntimeError('Require all planned cases')
for row in rows:
    episode = records/row['mode']/row['episode_id']
    state = json.loads((episode/'episode.json').read_text())
    events = [json.loads(s) for s in (episode/'events.jsonl').read_text().splitlines()]
    if state['status'] != 'closed' or not any(e['kind'] == 'episode_closed' for e in events):
        raise RuntimeError('Close all environments before private export')
with tarfile.open(root/'offline-json.tar.gz', 'w:gz') as archive:
    for file in sorted(records.rglob('*')):
        if file.is_file() and file.suffix in {'.json', '.jsonl'}:
            archive.add(file, arcname=file.relative_to(root))
print(json.dumps(dict(closed_episodes=len(rows), bytes=(root/'offline-json.tar.gz').stat().st_size)))
