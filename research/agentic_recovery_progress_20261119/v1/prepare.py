"""Create a new, non-overwriting freeze using the prior diagnostic protocol."""
import json
import runpy
from pathlib import Path

root = Path(__file__).resolve().parent
previous = root.parents[1]/'agentic_holding_model_20261119/v2'
if (root/'source').exists():
    raise RuntimeError('Already frozen; inspect this admission instead of replaying')
batch = (previous/'run.sbatch').read_text().replace('holding-model-v2', 'recovery-progress-v1')
batch = batch.replace('holding-model', 'recovery-progress').replace('8131', '8141').replace('8132', '8142')
(root/'run.sbatch').write_text(batch, encoding='utf-8', newline='\n')
freeze = (previous/'freeze.py').read_text().replace('18161', '18171').replace('18162', '18172')
(root/'freeze.py').write_text(freeze, encoding='utf-8')
for name in ['launch-when-ready.mjs', 'collect_admission.py', 'export_closed.py']:
    (root/name).write_text((previous/name).read_text(), encoding='utf-8')
deploy = (root.parents[1]/'agentic_experience_20261119/v2/deploy.py').read_text()
(root/'deploy.py').write_text(deploy, encoding='utf-8')
runpy.run_path(str(root/'freeze.py'), run_name='__main__')
protocol = json.loads((root/'protocol-intent.json').read_text())
protocol.update(purpose='Recovery progress regression using the same historical seed 70; two diagnostic conditions, not an algorithm comparator',
    changes=['authoritative per-segment public API attempts', 'whole-segment name preflight',
             'new grasp invalidates prior failed grasp evidence', 'task-fact recovery context and repeated-attempt guard',
             'separate model and system assistance origin; owner-confirmed hold before assistance'],
    progression='frame/time changes do not count; visible public centers quantized to centimetres for repetition only, never verification',
    guard_scope='exact failed code, repeated observe/finish; does not prove semantic equivalence of distinct Python programs')
(root/'protocol-intent.json').write_text(json.dumps(protocol, indent=2), encoding='utf-8')
