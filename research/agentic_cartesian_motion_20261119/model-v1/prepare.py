"""Freeze current motion contract for three autonomous, natural task trials."""
import hashlib
import json
import os
import shutil
import tarfile
from pathlib import Path

root = Path(__file__).resolve().parent
project = root.parents[2]
previous = project/'research/agentic_full_loop_20261119/v1'
source = root/'source'
if source.exists():
    raise RuntimeError('Already frozen; never replay an admitted trial')
batch = (previous/'run.sbatch').read_text().replace('full-loop-v1','cartesian-model-v1').replace('full-loop','cartesian-model').replace('8151','8161')
batch = batch.split('CAP_SUPPORT_SECOND_VIEW=robot0_eye_in_hand CUDA_VISIBLE_DEVICES=0 "$python_bin" -u -m experiments.active_holding_model_server')[0]
batch += 'wait "$baseline"\n'
(root/'run.sbatch').write_text(batch,encoding='utf-8',newline='\n')
for folder in ['runtime','cap-runtime/agentic_cap','cap-runtime/experiments']:
    for directory, subdirs, names in os.walk(project/folder):
        subdirs[:] = [n for n in subdirs if n not in {'node_modules','__pycache__','data'} and not n.startswith('.')]
        for name in names:
            file = Path(directory)/name
            if file.suffix not in {'.py','.mjs','.cjs','.json'} or '.test.' in name:
                continue
            target = source/file.relative_to(project)
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(file,target)
files = [dict(path=p.relative_to(source).as_posix(),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
         for p in sorted(source.rglob('*')) if p.is_file()]
with tarfile.open(root/'runtime-source.tar.gz','w:gz') as archive:
    archive.add(source/'cap-runtime',arcname='cap-runtime')
    archive.add(root/'run.sbatch',arcname='run.sbatch')
manifest = dict(files=files,archive_sha256=hashlib.sha256((root/'runtime-source.tar.gz').read_bytes()).hexdigest())
(root/'freeze.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
old = json.loads((previous/'normal-options.json').read_text())
options = dict(old,sourceRoot=str(source),output=str(root/'normal-campaign'),
               runtimeURL='http://127.0.0.1:18191',seeds=[83])
(root/'normal-options.json').write_text(json.dumps(options,indent=2),encoding='utf-8')
(root/'launch.mjs').write_text((previous/'launch.mjs').read_text().replace("['normal','active']", "['normal']"),encoding='utf-8')
(root/'status.mjs').write_text((previous/'status.mjs').read_text().replace("['normal','active']", "['normal']"),encoding='utf-8')
(root/'deploy.py').write_text((project/'research/agentic_cartesian_motion_20261119/physical-v1/deploy.py').read_text(),encoding='utf-8')
(root/'protocol-intent.json').write_text(json.dumps(dict(tasks=options['tasks'],seeds=[83],repeats=1,
    model=options['expectedModel'],experience='disabled',faults='none',
    purpose='Autonomous natural task feasibility after bounded IK and encoder/FK TCP acceptance',
    budgets=dict(decisions=12,calls=40,motion_steps=1500,wall_seconds=300),
    limitations=['One new seed per task, no statistical or causal algorithm comparison',
        'No reset during recovery; all environments close before independent scoring']),indent=2),encoding='utf-8')
print(json.dumps(dict(files=len(files),archive_sha256=manifest['archive_sha256'])))
