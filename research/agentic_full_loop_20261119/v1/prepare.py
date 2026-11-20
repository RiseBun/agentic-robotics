"""Freeze three natural tasks and one active-code fault, with explicit scope."""
import hashlib
import json
import os
import shutil
import tarfile
from pathlib import Path

root = Path(__file__).resolve().parent
project = root.parents[2]
previous = project/'research/agentic_recovery_progress_20261119/v1'
source = root/'source'
if source.exists():
    raise RuntimeError('Already frozen; no replay')
batch = (previous/'run.sbatch').read_text().replace('recovery-progress-v1', 'full-loop-v1')
batch = batch.replace('recovery-progress', 'full-loop').replace('8141', '8151').replace('8142', '8152')
batch = batch.replace(' --time=00:20:00', ' --time=00:30:00')
batch = batch.replace('CAP_SUPPORT_SECOND_VIEW=\'\'', "CAP_SUPPORT_SECOND_VIEW=robot0_eye_in_hand")
batch = batch.replace('CAP_PLACEMENT_EVIDENCE=1 CAP_SUPPORT_OCCLUSION=', 'CAP_PLACEMENT_EVIDENCE=1 CAP_HOLDING_WATCHDOG=1 CAP_SUPPORT_OCCLUSION=')
batch = batch.replace('-m experiments.holding_model_server \\\n    --port 8151 --mode recoverable', '-m agentic_cap.server \\\n    --port 8151 --backend cap --evaluation')
batch = batch.replace('-m experiments.holding_model_server \\\n    --port 8152 --mode occluded', '-m experiments.active_holding_model_server \\\n    --port 8152')
batch = batch.replace('recoverable-records', 'normal-records').replace('occluded-records', 'active-records')
(root/'run.sbatch').write_text(batch, encoding='utf-8', newline='\n')
for folder in ['runtime', 'cap-runtime/agentic_cap', 'cap-runtime/experiments']:
    for directory, subdirs, names in os.walk(project/folder):
        subdirs[:] = [n for n in subdirs if n not in {'node_modules','__pycache__','data'} and not n.startswith('.')]
        for name in names:
            file = Path(directory)/name
            if file.suffix not in {'.py','.mjs','.cjs','.json'} or '.test.' in name: continue
            target = source/file.relative_to(project)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file,target)
files = [dict(path=p.relative_to(source).as_posix(),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
         for p in sorted(source.rglob('*')) if p.is_file()]
with tarfile.open(root/'runtime-source.tar.gz','w:gz') as archive:
    archive.add(source/'cap-runtime',arcname='cap-runtime')
    archive.add(root/'run.sbatch',arcname='run.sbatch')
manifest = dict(files=files,archive_sha256=hashlib.sha256((root/'runtime-source.tar.gz').read_bytes()).hexdigest())
(root/'freeze.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
old = json.loads((previous/'options.json').read_text())
common = {k:old[k] for k in ['tokenFile','scratch','reuseEncryptedProfile','expectedModel']}
features = dict(inspect_support=True,inspect_placement=True,support_second_view='robot0_eye_in_hand',
                holding_watchdog=True,support_camera_fault='')
for variant in ['normal','active']:
    options = dict(common,sourceRoot=str(source),output=str(root/(variant+'-campaign')),
        runtimeURL='http://127.0.0.1:'+('18181' if variant=='normal' else '18182'),
        expectedRuntimeFeatures=dict(features,**({'diagnostic_fault':'active_rpc'} if variant=='active' else {})),
        tasks=['cube_lifting','cube_stack','cube_restack'] if variant=='normal' else ['cube_stack'],
        seeds=[73] if variant=='normal' else [74],repeats=1,maxTrials=3 if variant=='normal' else 1,
        settleMilliseconds=1500)
    (root/(variant+'-options.json')).write_text(json.dumps(options,indent=2),encoding='utf-8')
(root/'deploy.py').write_text((previous/'deploy.py').read_text(),encoding='utf-8')
protocol = dict(normal_tasks=['cube_lifting','cube_stack','cube_restack'],normal_seed=73,active_task='cube_stack',active_seed=74,
    model=common['expectedModel'],experience='disabled',default_policy='current development recovery context and guard enabled',
    active_fault='One finite actuator release during five-second public RPC latency in a new model segment after prior verified red hold',
    limitations=['One trial per case; not a statistical or algorithm comparison','Active code waiting for RPC; not uninterrupted moving-arm slip detection',
                 'No scene reset during recovery; all cases closed before private scoring; preserve failed coverage as failure to cover'],
    budgets=dict(decisions=12,calls=40,motion_steps=1500,wall_seconds=300))
(root/'protocol-intent.json').write_text(json.dumps(protocol,indent=2),encoding='utf-8')
print(json.dumps(dict(files=len(files),archive_sha256=manifest['archive_sha256'])))
