import runpy
from pathlib import Path

root=Path(__file__).resolve().parent
project=root.parents[2]
previous=project/'research/agentic_support_controls_20261119/v1'
if (root/'source').exists():
    raise RuntimeError('Already frozen; no replay')
batch=(previous/'run.sbatch').read_text().replace('support-controls-v1','cartesian-support-controls-v1').replace('support-controls','cartesian-support-controls')
# The second replacement above also changes the folder token; make it explicit.
batch=batch.replace('cartesian-cartesian-support-controls-v1','cartesian-support-controls-v1')
(root/'run.sbatch').write_text(batch,encoding='utf-8',newline='\n')
(root/'freeze.py').write_text((previous/'freeze.py').read_text(),encoding='utf-8')
(root/'deploy.py').write_text((previous/'deploy.py').read_text().replace('afterany:20780','afterany:20788'),encoding='utf-8')
(root/'export_closed.py').write_text((previous/'export_closed.py').read_text(),encoding='utf-8')
(root/'audit_closed.py').write_text((previous/'audit_closed.py').read_text(),encoding='utf-8')
runpy.run_path(str(root/'freeze.py'),run_name='__main__')
