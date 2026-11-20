import runpy
from pathlib import Path

root = Path(__file__).resolve().parent
project = root.parents[2]
previous = project / 'research/agentic_support_controls_20261119/v1'
if (root/'source').exists():
    raise RuntimeError('Already frozen')
batch = (previous/'run.sbatch').read_text().replace('support-controls-v1', 'cartesian-contract-v1')
batch = batch.replace('support-controls', 'cartesian-contract').replace('experiments.support_controls_trial', 'experiments.cartesian_contract_trial')
(root/'run.sbatch').write_text(batch,encoding='utf-8',newline='\n')
(root/'freeze.py').write_text((previous/'freeze.py').read_text(),encoding='utf-8')
deploy = (previous/'deploy.py').read_text().replace("'--dependency=afterany:20780', ", '')
(root/'deploy.py').write_text(deploy,encoding='utf-8')
runpy.run_path(str(root/'freeze.py'),run_name='__main__')
