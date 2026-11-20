"""New frozen unbound-name handoff; old failed natural cases remain unchanged."""
import hashlib
import json
import os
import shutil
import tarfile
from pathlib import Path

root=Path(__file__).resolve().parent
previous=root.parent/'v1'
project=root.parents[2]
source=root/'source'
if source.exists():
    raise RuntimeError('Already frozen; never replay admitted preparation')
prefix=(previous/'run.sbatch').read_text().split('CAP_SUPPORT_SECOND_VIEW=robot0_eye_in_hand CUDA_VISIBLE_DEVICES=0 "$python_bin" -u -m agentic_cap.server')[0]
batch=prefix.replace('program-repair-v1','program-repair-v3').replace('--job-name=program-repair','--job-name=program-repair-unbound')
batch=batch.replace('program-repair-%j.log','program-repair-unbound-%j.log')
batch+='''CAP_SUPPORT_SECOND_VIEW=robot0_eye_in_hand CUDA_VISIBLE_DEVICES=0 "$python_bin" -u -m experiments.program_repair_model_server \\
    --port 8212 --mode unbound_name --seed 88 --upstream "$project/third_party/cap-x" \\
    --records "$root/repair-records" > "$root/candidate.log" 2>&1 & candidate=$!
wait "$candidate"
'''
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
old=json.loads((previous/'repair-options.json').read_text())
options=dict(old,sourceRoot=str(source),output=str(root/'repair-campaign'),runtimeURL='http://127.0.0.1:18232',
    programRepairEpisodes=[dict(url='http://127.0.0.1:18232',mode='unbound_name')])
(root/'repair-options.json').write_text(json.dumps(options,indent=2),encoding='utf-8')
launch=(previous/'launch.mjs').read_text().replace("const names=['repair','normal'];","const names=['repair'];").replace("diagnostic.mode!=='field_access'","diagnostic.mode!=='unbound_name'")
(root/'launch.mjs').write_text(launch,encoding='utf-8')
(root/'deploy.py').write_text((previous/'deploy.py').read_text(),encoding='utf-8')
(root/'protocol-intent.json').write_text(json.dumps(dict(task='cube_restack',seed=88,mode='unbound_name',
    model=options['expectedModel'],purpose='One rejected unbound-name program, then original-goal repair by the product model',
    experience='disabled',budgets=dict(decisions=12,calls=40,motion_steps=1500,wall_seconds=300),
    limitations=['Fixed failure preparation, not a natural task benchmark','No comparison or statistical claim',
      'Old failed trials retained; no reset or budget clearing during model recovery']),indent=2),encoding='utf-8')
print(json.dumps(dict(files=len(files),archive_sha256=manifest['archive_sha256'])))
