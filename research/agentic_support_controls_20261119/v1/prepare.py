import runpy
from pathlib import Path

root=Path(__file__).resolve().parent
project=root.parents[2]
previous=project/'research/agentic_program_preflight_20261119/v2'
if (root/'source').exists(): raise RuntimeError('Already frozen; no replay')
batch=(previous/'run.sbatch').read_text().replace('program-preflight-v2','support-controls-v1')
batch=batch.replace('program-preflight','support-controls').replace('experiments.program_preflight_trial','experiments.support_controls_trial')
(root/'run.sbatch').write_text(batch,encoding='utf-8',newline='\n')
freeze=(previous/'freeze.py').read_text().replace('preflight-source.tar.gz','runtime-source.tar.gz')
(root/'freeze.py').write_text(freeze,encoding='utf-8')
deploy=(project/'research/agentic_recovery_progress_20261119/v1/deploy.py').read_text()
deploy=deploy.replace("['sbatch', '--parsable'", "['sbatch', '--dependency=afterany:20780', '--parsable'")
(root/'deploy.py').write_text(deploy,encoding='utf-8')
runpy.run_path(str(root/'freeze.py'),run_name='__main__')
