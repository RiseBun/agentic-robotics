import hashlib
import json
import shutil
import tarfile
from pathlib import Path
root = Path(__file__).resolve().parent
project = root.parents[2]
source = root / 'source'
if source.exists(): raise RuntimeError('Refuse to overwrite frozen source')
files = []
for folder in ['agentic_cap', 'experiments']:
    for file in sorted((project / 'cap-runtime' / folder).glob('*.py')):
        target = source / file.relative_to(project)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(file, target)
        files.append(dict(path=file.relative_to(project).as_posix(), sha256=hashlib.sha256(file.read_bytes()).hexdigest()))
with tarfile.open(root / 'watchdog-source.tar.gz', 'w:gz') as archive:
    archive.add(source / 'cap-runtime', arcname='cap-runtime')
    archive.add(root / 'run.sbatch', arcname='run.sbatch')
manifest = dict(files=files, archive_sha256=hashlib.sha256((root / 'watchdog-source.tar.gz').read_bytes()).hexdigest())
(root / 'freeze.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
print(json.dumps(dict(archive_sha256=manifest['archive_sha256'], files=len(files))))
