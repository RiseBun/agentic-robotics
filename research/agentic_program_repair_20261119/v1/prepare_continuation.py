"""Run only verified-unadmitted natural cases; original diagnosis remains terminal."""
import json
from pathlib import Path

root=Path(__file__).resolve().parent
audit=json.loads((root/'interruption-audit.json').read_text())
if audit['normal'] or len(audit['repair'])!=1 or audit['repair'][0]['status']!='closed':
    raise RuntimeError('Original admission is not fully audited')
if (root/'normal-campaign/campaign.json').exists():
    raise RuntimeError('Natural campaign already admitted; do not replay')
batch=(root/'run.sbatch').read_text().replace('--job-name=program-repair','--job-name=program-repair-natural')
batch=batch.replace('program-repair-%j.log','program-repair-natural-%j.log')
batch=batch.split('CAP_SUPPORT_SECOND_VIEW=robot0_eye_in_hand CUDA_VISIBLE_DEVICES=0 "$python_bin" -u -m experiments.program_repair_model_server')[0]
batch += 'wait "$baseline"\n'
(root/'continue-natural.sbatch').write_text(batch,encoding='utf-8',newline='\n')
previous=root.parents[1]/'agentic_cartesian_motion_20261119/model-v1/launch.mjs'
# This launcher admits only the existing natural options and frozen source.
(root/'launch-natural.mjs').write_text(previous.read_text(),encoding='utf-8')
