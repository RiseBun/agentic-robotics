"""Freeze sources and protocol before any new physical episode is admitted."""
import hashlib
import json
import os
import shutil
import tarfile
from pathlib import Path

root = Path(__file__).resolve().parent
project = root.parents[2]
source = root / 'source'
if source.exists():
    raise RuntimeError('Frozen source already exists; do not overwrite a campaign')
for folder in ['runtime', 'cap-runtime/agentic_cap', 'cap-runtime/experiments']:
    for directory, subdirs, names in os.walk(project / folder):
        subdirs[:] = [n for n in subdirs if n not in {'node_modules', '__pycache__', 'data'} and not n.startswith('.')]
        for name in names:
            file = Path(directory) / name
            if file.suffix not in {'.py', '.mjs', '.cjs', '.json'} or '.test.' in name:
                continue
            output = source / file.relative_to(project)
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, output)
hashes = [{'path': p.relative_to(source).as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
          for p in sorted(source.rglob('*')) if p.is_file()]
with tarfile.open(root / 'runtime-source.tar.gz', 'w:gz') as archive:
    archive.add(source / 'cap-runtime', arcname='cap-runtime')
    archive.add(root / 'run.sbatch', arcname='run.sbatch')
manifest = dict(files=hashes, archive_sha256=hashlib.sha256((root / 'runtime-source.tar.gz').read_bytes()).hexdigest())
(root / 'freeze.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
previous = json.loads((project / 'research/agentic_vertical_retreat_model_20261119/v1/options.json').read_text())
options = {k: previous[k] for k in ['tokenFile', 'scratch', 'reuseEncryptedProfile', 'expectedModel']}
options.update(sourceRoot=str(source), output=str(root / 'campaign'), experienceRoot=str(root / 'corpus'),
               retreatEpisodes=[dict(mode=mode, url=f'http://127.0.0.1:{port}', fault_mode='retreat_recovers', experience=use)
                                for mode, port, use in [('baseline', 18147, False), ('candidate', 18148, True)]])
(root / 'options.json').write_text(json.dumps(options, indent=2), encoding='utf-8')
(root / 'protocol-intent.json').write_text(json.dumps(dict(task='cube_restack', seed=65,
    order=['baseline', 'candidate'], preparation='fixed public controller, includes clearing green from red',
    experience_source='independently scored old cube_stack/62 recovery only; no current trial admission',
    comparison='same source, model, prompt, APIs, fault and original cumulative budgets; only retrieval differs',
    limitations=['One pair, sequential execution; waiting cost counted, no latency causality claim',
                 'Artificial failure handoff; no natural or end-to-end success-rate claim',
                 'Predefined repair extraction; no general skill induction or RSI']), indent=2), encoding='utf-8')
print(json.dumps(dict(archive_sha256=manifest['archive_sha256'], files=len(hashes))))
