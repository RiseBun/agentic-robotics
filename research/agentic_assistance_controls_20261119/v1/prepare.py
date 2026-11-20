import hashlib
import json
import os
import shutil
from pathlib import Path

root=Path(__file__).resolve().parent
project=root.parents[2]
if (root/'source').exists(): raise RuntimeError('Already frozen; do not replay')
for folder in ['runtime','cap-runtime/agentic_cap']:
    for directory,subdirs,names in os.walk(project/folder):
        subdirs[:]=[n for n in subdirs if n not in {'node_modules','__pycache__','data'} and not n.startswith('.')]
        for name in names:
            file=Path(directory)/name
            if file.suffix not in {'.mjs','.cjs','.py','.json'} or '.test.' in name: continue
            target=root/'source'/file.relative_to(project)
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(file,target)
files=[dict(path=p.relative_to(root/'source').as_posix(),sha256=hashlib.sha256(p.read_bytes()).hexdigest())
       for p in sorted((root/'source').rglob('*')) if p.is_file()]
(root/'freeze.json').write_text(json.dumps(dict(files=files),indent=2),encoding='utf-8')
previous=json.loads((project/'research/agentic_full_loop_20261119/v1/normal-options.json').read_text())
options=dict(runtimeURL=previous['runtimeURL'],tokenFile=previous['tokenFile'],sourceRoot=str(root/'source'),
    cases=[dict(mode='stop',seed=77),dict(mode='goal_change',seed=78)],
    scope='scripted decisions over real Runtime HTTP and CaP; no LLM skill/diagnosis claim; five-second response latency simulated only after actual owner-confirmed hold')
(root/'options.json').write_text(json.dumps(options,indent=2),encoding='utf-8')
print(json.dumps(dict(files=len(files),cases=2)))
