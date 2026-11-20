"""Freeze one program-error handoff and three natural model tasks, separately scored."""
import hashlib
import json
import os
import shutil
import tarfile
from pathlib import Path

root=Path(__file__).resolve().parent
project=root.parents[2]
previous=project/'research/agentic_full_loop_20261119/v1'
source=root/'source'
if source.exists():
    raise RuntimeError('Already frozen; never replay admitted trials')
batch=(previous/'run.sbatch').read_text().replace('full-loop-v1','program-repair-v1').replace('full-loop','program-repair')
batch=batch.replace('8151','8181').replace('8152','8182')
batch=batch.replace('experiments.active_holding_model_server','experiments.program_repair_model_server')
batch=batch.replace('--port 8182 --upstream','--port 8182 --seed 84 --upstream').replace('active-records','repair-records')
(root/'run.sbatch').write_text(batch,encoding='utf-8',newline='\n')
for folder in ['runtime','cap-runtime/agentic_cap','cap-runtime/experiments']:
    for directory,subdirs,names in os.walk(project/folder):
        subdirs[:]=[n for n in subdirs if n not in {'node_modules','__pycache__','data'} and not n.startswith('.')]
        for name in names:
            file=Path(directory)/name
            if file.suffix not in {'.py','.mjs','.cjs','.json'} or '.test.' in name:
                continue
            target=source/file.relative_to(project)
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(file,target)
files=[dict(path=p.relative_to(source).as_posix(),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
       for p in sorted(source.rglob('*')) if p.is_file()]
with tarfile.open(root/'runtime-source.tar.gz','w:gz') as archive:
    archive.add(source/'cap-runtime',arcname='cap-runtime')
    archive.add(root/'run.sbatch',arcname='run.sbatch')
manifest=dict(files=files,archive_sha256=hashlib.sha256((root/'runtime-source.tar.gz').read_bytes()).hexdigest())
(root/'freeze.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
old=json.loads((previous/'normal-options.json').read_text())
normal=dict(old,sourceRoot=str(source),output=str(root/'normal-campaign'),runtimeURL='http://127.0.0.1:18201',seeds=[85])
repair={k:normal[k] for k in ['sourceRoot','tokenFile','scratch','reuseEncryptedProfile','expectedModel','expectedRuntimeFeatures']}
repair.update(output=str(root/'repair-campaign'),runtimeURL='http://127.0.0.1:18202',
              programRepairEpisodes=[dict(url='http://127.0.0.1:18202',mode='field_access')])
for name,options in [('normal',normal),('repair',repair)]:
    (root/(name+'-options.json')).write_text(json.dumps(options,indent=2),encoding='utf-8')
(root/'deploy.py').write_text((project/'research/agentic_cartesian_motion_20261119/physical-v1/deploy.py').read_text(),encoding='utf-8')
(root/'protocol-intent.json').write_text(json.dumps(dict(model=normal['expectedModel'],
    diagnostic=dict(task='cube_restack',seed=84,preparation='public green grasp; one read-only API then invalid state[0]; same-scene model repair'),
    natural=dict(tasks=normal['tasks'],seeds=[85],repeats=1),experience='disabled',
    budgets=dict(decisions=12,calls=40,motion_steps=1500,wall_seconds=300),
    limitations=['No paired baseline or statistical claim','Never merge prepared diagnosis with natural task success rate',
        'No environment or budget reset; all four episodes close before private scoring']),indent=2),encoding='utf-8')
print(json.dumps(dict(files=len(files),archive_sha256=manifest['archive_sha256'])))
