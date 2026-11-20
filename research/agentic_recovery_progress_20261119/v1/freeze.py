import hashlib
import json
import os
import shutil
import tarfile
from pathlib import Path

root = Path(__file__).resolve().parent
project = root.parents[2]
source = root/'source'
if source.exists(): raise RuntimeError('Already frozen; never replay')
for folder in ['runtime', 'cap-runtime/agentic_cap', 'cap-runtime/experiments']:
    for directory, subdirs, names in os.walk(project/folder):
        subdirs[:] = [n for n in subdirs if n not in {'node_modules','__pycache__','data'} and not n.startswith('.')]
        for name in names:
            file = Path(directory)/name
            if file.suffix not in {'.py','.mjs','.cjs','.json'} or '.test.' in name: continue
            target = source/file.relative_to(project)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, target)
files = [dict(path=p.relative_to(source).as_posix(), sha256=hashlib.sha256(p.read_bytes()).hexdigest())
         for p in sorted(source.rglob('*')) if p.is_file()]
with tarfile.open(root/'runtime-source.tar.gz', 'w:gz') as archive:
    archive.add(source/'cap-runtime', arcname='cap-runtime')
    archive.add(root/'run.sbatch', arcname='run.sbatch')
manifest = dict(files=files, archive_sha256=hashlib.sha256((root/'runtime-source.tar.gz').read_bytes()).hexdigest())
(root/'freeze.json').write_text(json.dumps(manifest, indent=2))
previous = json.loads((project/'research/agentic_multiview_model_20261119/v1/options.json').read_text())
options = {k: previous[k] for k in ['tokenFile','scratch','reuseEncryptedProfile','expectedModel']}
modes = ['recoverable','occluded']
schedule = [dict(pair_id='stack-s70', task='cube_stack', seed=70, variant=mode) for mode in modes]
features = {mode: dict(inspect_support=True, inspect_placement=True, support_second_view='robot0_eye_in_hand',
    holding_watchdog=True, support_camera_fault='', diagnostic_fault=mode) for mode in modes}
options.update(sourceRoot=str(source), output=str(root/'campaign'), variants={m:str(source) for m in modes},
    schedule=schedule, runtimeFeatures=features,
    runtimeURLs=dict(recoverable='http://127.0.0.1:18171', occluded='http://127.0.0.1:18172'), settleMilliseconds=1250)
(root/'options.json').write_text(json.dumps(options, indent=2))
(root/'protocol-intent.json').write_text(json.dumps(dict(schedule=schedule, model=options['expectedModel'],
    controller='product model from initial scene; no fixed preparation or handoff',
    fault='one actual actuator release for thirty steps, scheduled ten steps after first verified red hold and idle',
    occluded='mask red public RGB-D in both cameras after monitor hold confirmation; never clear automatically',
    budgets=dict(decisions=12,calls=40,motion_steps=1500,wall_seconds=300), experience='disabled',
    admission='retain all outcomes, no reset/replay; private scoring only after both episodes closed',
    limitations=['Two fault conditions once each; no algorithm comparator or statistical claim',
                 'Injected actuator/sensor faults, not natural slip or collision',
                 'Actual model/thinking/action phase at detection audited from events, not assumed']), indent=2))
print(json.dumps(dict(archive_sha256=manifest['archive_sha256'], files=len(files))))
