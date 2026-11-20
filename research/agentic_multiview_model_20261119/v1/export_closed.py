"""Offline export: a complete host admission list and authoritative closure required."""
import json
import sys
import tarfile
from pathlib import Path

root = Path(sys.argv[1]).resolve()
admitted = json.loads((root / 'closed-admission.json').read_text())
if len(admitted) != 8 or len({(r['pair_id'], r['variant']) for r in admitted}) != 8:
    raise RuntimeError('Require all eight original model results')
known = set()
for row in admitted:
    if row['variant'] not in {'baseline', 'candidate'}: raise RuntimeError('Unexpected variant')
    identity = row.get('episode_id')
    if identity is None:
        if row['outcome'] != 'infrastructure_error': raise RuntimeError('Missing episode identity')
        continue
    if len(identity) != 32 or any(c not in '0123456789abcdef' for c in identity): raise RuntimeError('Invalid identity')
    directory = root / (row['variant'] + '-records') / identity
    state = json.loads((directory / 'episode.json').read_text())
    events = [json.loads(s) for s in (directory / 'events.jsonl').read_text().splitlines()]
    if state['status'] != 'closed' or not any(e['kind'] == 'episode_closed' for e in events):
        raise RuntimeError('Every admitted environment must be closed before private export')
    known.add(directory)
for variant in ['baseline', 'candidate']:
    actual = {p.parent for p in (root / (variant + '-records')).glob('*/episode.json')}
    if actual - known: raise RuntimeError('Unaccounted environment; inspect rather than replay')
with tarfile.open(root / 'offline-json.tar.gz', 'w:gz') as archive:
    for directory in sorted(known):
        for file in sorted(directory.glob('*')):
            if file.is_file() and file.suffix in {'.json', '.jsonl'}:
                archive.add(file, arcname=file.relative_to(root))
print(json.dumps(dict(closed_episodes=len(known), admitted_trials=len(admitted),
                     archive_bytes=(root/'offline-json.tar.gz').stat().st_size)))
