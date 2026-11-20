"""Read-only narrow source inspection; never recurse through bundled web assets."""
import hashlib
import json
import sys
from pathlib import Path
root=Path(sys.argv[1]).resolve()/'capx/integrations/franka'
rows=[]
for file in sorted(root.rglob('*.py')):
    if file.name not in {'control.py','control_reduced.py'}:continue
    if any(part in {'node_modules','.venv','__pycache__'} for part in file.parts):continue
    source=file.read_text(encoding='utf-8')
    lines=source.splitlines()
    hits=[i for i,line in enumerate(lines) if 'def goto_pose(' in line]
    for line in hits:
        rows.append(dict(path=str(file),sha256=hashlib.sha256(file.read_bytes()).hexdigest(),
            line=line+1,excerpt='\n'.join(lines[max(0,line-5):line+65])))
if len(rows)>8:raise RuntimeError('Unexpected source hit count; narrow search')
print(json.dumps(rows,indent=2))
