"""Export finite diagnostic records only after every planned episode is closed."""
import json
import re
import sys
import tarfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
records = root / 'records'
rows = json.loads((records / 'results.json').read_text())
if [row['mode'] for row in rows] != ['single', 'dual', 'dual_masked']:
    raise RuntimeError('Require all planned cases before exporting private scoring records')
for row in rows:
    episode = records / row['mode'] / row['episode_id']
    state = json.loads((episode / 'episode.json').read_text())
    events = [json.loads(line) for line in (episode / 'events.jsonl').read_text().splitlines()]
    if state['status'] != 'closed' or not any(e['kind'] == 'episode_closed' for e in events):
        raise RuntimeError('All episodes must be closed before export')

# Keep the initial post-activation evidence cited by results, rather than all frames.
refs = set()
def collect(value):
    if isinstance(value, dict):
        for child in value.values(): collect(child)
    elif isinstance(value, list):
        for child in value: collect(child)
    elif isinstance(value, str) and re.fullmatch(r'(?:frame-\d+|view-[A-Za-z0-9_-]+-\d+)\.(?:png|npz)', value):
        stem = Path(value).stem
        refs.update({stem + '.png', stem + '.npz'})
collect(rows)
output = root / 'closed-evidence.tar.gz'
with tarfile.open(output, 'w:gz') as archive:
    for file in sorted(records.rglob('*')):
        if file.is_file() and (file.suffix in {'.json', '.jsonl'} or file.name in refs):
            archive.add(file, arcname=file.relative_to(root))
print(json.dumps({'closed_episodes': len(rows), 'archive_bytes': output.stat().st_size}))
