"""Admit a new natural-task paired experiment; never overwrite old records."""
import hashlib
import json
import os
import shutil
import tarfile
from pathlib import Path

root = Path(__file__).resolve().parent
project = root.parents[2]
source = root / 'source'
if source.exists(): raise RuntimeError('Already frozen; do not replay')
for folder in ['runtime', 'cap-runtime/agentic_cap', 'cap-runtime/experiments']:
    for directory, subdirs, names in os.walk(project / folder):
        subdirs[:] = [n for n in subdirs if n not in {'node_modules', '__pycache__', 'data'} and not n.startswith('.')]
        for name in names:
            file = Path(directory) / name
            if file.suffix not in {'.py', '.mjs', '.cjs', '.json'} or '.test.' in name: continue
            output = source / file.relative_to(project)
            output.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, output)
files = [{'path': p.relative_to(source).as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
         for p in sorted(source.rglob('*')) if p.is_file()]
with tarfile.open(root / 'runtime-source.tar.gz', 'w:gz') as archive:
    archive.add(source / 'cap-runtime', arcname='cap-runtime')
    archive.add(root / 'run.sbatch', arcname='run.sbatch')
manifest = dict(files=files, archive_sha256=hashlib.sha256((root / 'runtime-source.tar.gz').read_bytes()).hexdigest())
(root / 'freeze.json').write_text(json.dumps(manifest, indent=2))
previous = json.loads((project / 'research/agentic_experience_20261119/v2/options.json').read_text())
options = {k: previous[k] for k in ['tokenFile', 'scratch', 'reuseEncryptedProfile', 'expectedModel']}
schedule = []
for index, (task, seed) in enumerate([(t, s) for s in [67, 68] for t in ['cube_stack', 'cube_restack']]):
    for variant in (['baseline', 'candidate'] if index % 2 == 0 else ['candidate', 'baseline']):
        schedule.append(dict(pair_id=f'{task[5:]}-s{seed}', task=task, seed=seed, variant=variant))
features = {variant: dict(inspect_support=True, inspect_placement=True, support_camera_fault='',
                         support_second_view='' if variant == 'baseline' else 'robot0_eye_in_hand')
            for variant in ['baseline', 'candidate']}
options.update(sourceRoot=str(source), output=str(root / 'campaign'),
               variants={v: str(source) for v in features}, runtimeFeatures=features,
               runtimeURLs=dict(baseline='http://127.0.0.1:18151', candidate='http://127.0.0.1:18152'),
               schedule=schedule, settleMilliseconds=1250)
(root / 'options.json').write_text(json.dumps(options, indent=2))
(root / 'protocol-intent.json').write_text(json.dumps(dict(schedule=schedule,
    preparation='none; actual product model receives goal from initial environment',
    experience='disabled', budgets=dict(decisions=12, calls=40, motion_steps=1500, wall_seconds=300),
    comparison='same sources, model, prompt, skills, thresholds and budgets; secondary calibrated RGB-D sensor differs',
    admission='all cases retained, no replay; all episodes closed before private scoring',
    limitations=['Two seeds, one trial per condition; no statistical significance claim',
                 'Candidate receives extra visual information; measures system capability, not planner-only improvement',
                 'Natural scenes only; cancellation and persistent sensor-fault tests reported separately']), indent=2))
print(json.dumps(dict(archive_sha256=manifest['archive_sha256'], files=len(files))))
