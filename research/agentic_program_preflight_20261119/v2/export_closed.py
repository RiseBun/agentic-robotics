"""Export the single frozen physical regression only after authoritative closure."""
import json
import sys
import tarfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
result = json.loads((root/'records/result.json').read_text())
identity = result['episode_id']
if len(identity) != 32 or any(c not in '0123456789abcdef' for c in identity):
    raise RuntimeError('Invalid episode identity')
episodes = list((root/'records').glob('*/episode.json'))
if len(episodes) != 1 or episodes[0].parent.name != identity:
    raise RuntimeError('Unexpected admitted episode; inspect without replay')
directory = episodes[0].parent
state = json.loads(episodes[0].read_text())
events = [json.loads(line) for line in (directory/'events.jsonl').read_text().splitlines()]
if state['status'] != 'closed' or not any(e['kind'] == 'episode_closed' for e in events):
    raise RuntimeError('Authoritative closure required before private export')
with tarfile.open(root/'offline-json.tar.gz', 'w:gz') as archive:
    for file in sorted((root/'records').rglob('*')):
        if file.is_file() and file.suffix in {'.json', '.jsonl'}:
            archive.add(file, arcname=file.relative_to(root))
print(json.dumps(dict(closed_episodes=1, episode_id=identity,
                     archive_bytes=(root/'offline-json.tar.gz').stat().st_size)))
