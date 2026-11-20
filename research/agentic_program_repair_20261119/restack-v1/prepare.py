"""Explicit new restack seed; retain the prior seed-85 transport failure unchanged."""
import hashlib
import json
from pathlib import Path

root=Path(__file__).resolve().parent
previous=root.parent/'v1'
if (root/'run.sbatch').exists():
    raise RuntimeError('Already prepared; no replay')
audit=json.loads((previous/'host-terminal-audit.json').read_text())
if audit['status']!='failed':
    raise RuntimeError('Require original interrupted host audit')
old=json.loads((previous/'normal-options.json').read_text())
options=dict(old,output=str(root/'normal-campaign'),runtimeURL='http://127.0.0.1:18211',
    tasks=['cube_restack'],seeds=[86],maxTrials=1)
(root/'normal-options.json').write_text(json.dumps(options,indent=2),encoding='utf-8')
batch=(previous/'continue-natural.sbatch').read_text()
batch=batch.replace('--job-name=program-repair-natural','--job-name=program-repair-restack')
batch=batch.replace('program-repair-natural-%j.log','program-repair-restack-%j.log')
batch=batch.replace('root="$project/agentic-mvp/program-repair-v1"',
    'root="$project/agentic-mvp/program-repair-restack-v1"')
batch=batch.replace('PYTHONPATH="$root/cap-runtime:',
    'PYTHONPATH="$project/agentic-mvp/program-repair-v1/cap-runtime:')
batch=batch.replace('8181','8191')
(root/'run.sbatch').write_text(batch,encoding='utf-8',newline='\n')
launch=(previous/'launch-natural.mjs').read_text().replace("'./source/runtime/", "'../v1/source/runtime/")
(root/'launch.mjs').write_text(launch,encoding='utf-8')
(root/'status.mjs').write_text((previous.parent.parent/'agentic_cartesian_motion_20261119/model-v1/status.mjs').read_text(),encoding='utf-8')
(root/'protocol-intent.json').write_text(json.dumps(dict(tasks=['cube_restack'],seed=86,repeats=1,
    model=options['expectedModel'],source_root=options['sourceRoot'],
    original_freeze_sha256=json.loads((previous/'freeze.json').read_text())['archive_sha256'],
    batch_sha256=hashlib.sha256((root/'run.sbatch').read_bytes()).hexdigest(),
    limitations=['New trial, not a resume or replacement for the interrupted seed-85 case',
      'Same frozen policy and Runtime; no paired baseline or statistical claim',
      'No reset or budget clearing within the task; score only after authoritative close']),indent=2),encoding='utf-8')
