"""Narrow public admission/closure audit after a transport interruption."""
import json
import sys
import tarfile
from pathlib import Path

root=Path(sys.argv[1]).resolve()
known={}
for variant in ['normal','repair']:
    directory=root/(variant+'-records')
    known[variant]=[]
    for file in directory.glob('*/episode.json'):
        state=json.loads(file.read_text())
        events=[json.loads(s) for s in (file.parent/'events.jsonl').read_text().splitlines()]
        if state['status']!='closed' or events[-1]['kind']!='episode_closed':
            raise RuntimeError('Episode still open; do not restart')
        known[variant].append(dict(episode_id=state['id'],status=state['status'],
            termination_reason=state.get('termination_reason'),used=state['used'],
            last_event_id=events[-1]['id']))
if len(known['repair'])!=1 or known['normal']:
    raise RuntimeError('Unexpected original admission; inspect before continuing')
(root/'interruption-audit.json').write_text(json.dumps(known,indent=2))
with tarfile.open(root/'closed-diagnostic-json.tar.gz','w:gz') as archive:
    for file in sorted((root/'repair-records').rglob('*')):
        if file.is_file() and file.suffix in {'.json','.jsonl'}:
            archive.add(file,arcname=file.relative_to(root))
print(json.dumps(known))
