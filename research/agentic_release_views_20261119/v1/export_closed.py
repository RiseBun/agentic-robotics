import json
import sys
import tarfile
from pathlib import Path
root = Path(sys.argv[1])
record = json.loads((root/'records/result.json').read_text())
episode = root/'records'/record['episode_id']
state = json.loads((episode/'episode.json').read_text())
if state['status'] != 'closed': raise RuntimeError('Close before export')
with tarfile.open(root/'probe-evidence.tar.gz', 'w:gz') as archive:
    for file in sorted((root/'records').rglob('*')):
        if file.is_file() and (file.suffix in {'.json','.jsonl'} or file.name.startswith('view-') and file.suffix in {'.png','.npz'}):
            archive.add(file, arcname=file.relative_to(root))
print(json.dumps(dict(episode_id=state['id'], status=state['status'], probe_status=record['status'])))
