"""Freeze ordinary task regression after audit/context separation."""
import hashlib
import json
import os
import shutil
import tarfile
from pathlib import Path

root=Path(__file__).resolve().parent
project=root.parents[2]
old=project/'research/agentic_program_repair_20261119'
source=root/'source'
if source.exists():
    raise RuntimeError('Already frozen; inspect original admission')
batch=(old/'v3/run.sbatch').read_text().split('CAP_SUPPORT_SECOND_VIEW=robot0_eye_in_hand CUDA_VISIBLE_DEVICES=0')[0]
batch=batch.replace('program-repair-v3','program-context-v1').replace('program-repair-unbound','program-context')
batch+='''CAP_SUPPORT_SECOND_VIEW=robot0_eye_in_hand CUDA_VISIBLE_DEVICES=0 "$python_bin" -u -m agentic_cap.server \\
    --port 8222 --upstream "$project/third_party/cap-x" \\
    --records "$root/normal-records" > "$root/normal.log" 2>&1 & candidate=$!
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
options=json.loads((old/'v1/normal-options.json').read_text())
options.update(sourceRoot=str(source),output=str(root/'normal-campaign'),runtimeURL='http://127.0.0.1:18242',seeds=[89])
(root/'normal-options.json').write_text(json.dumps(options,indent=2),encoding='utf-8')
launch=(old/'restack-v1/launch.mjs').read_text().replace('../v1/source/','./source/')
(root/'launch.mjs').write_text(launch,encoding='utf-8')
for name in ['deploy.py','collect_admission.py','export_closed.py']:
    (root/name).write_bytes((old/'v1'/name).read_bytes())
(root/'protocol-intent.json').write_text(json.dumps(dict(tasks=options['tasks'],seeds=[89],repeats=1,
    model=options['expectedModel'],experience='disabled',fault_injection='none',
    budgets=dict(decisions=12,calls=40,motion_steps=1500,wall_seconds=300),
    changes=['Zero-API unbound-name errors use focused repair context with the same model',
      'Raw event streams remain persisted but are not duplicated into decision history'],
    scope='Current-version ordinary task regression, no baseline or causal benefit claim'),indent=2),encoding='utf-8')
print(json.dumps(dict(files=len(files),archive_sha256=manifest['archive_sha256'])))
