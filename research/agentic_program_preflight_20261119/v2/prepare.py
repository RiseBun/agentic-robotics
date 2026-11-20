"""New freeze with an explicit settle window; the v1 outcome stays unknown."""
import runpy
from pathlib import Path

root = Path(__file__).resolve().parent
previous = root.parent/'v1'
if (root/'source').exists():
    raise RuntimeError('Already frozen; never replay an admitted trial')
(root/'run.sbatch').write_text((previous/'run.sbatch').read_text().replace('program-preflight-v1', 'program-preflight-v2'),
                              encoding='utf-8', newline='\n')
for name in ['freeze.py', 'export_closed.py']:
    (root/name).write_text((previous/name).read_text(), encoding='utf-8')
scorer = (previous/'score_closed.py').read_text().replace('job=20774',
    "job=int(json.loads((root/'admission.json').read_text())['job_id'])")
(root/'score_closed.py').write_text(scorer, encoding='utf-8')
runpy.run_path(str(root/'freeze.py'), run_name='__main__')
