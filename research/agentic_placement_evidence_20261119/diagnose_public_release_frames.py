"""Read old CLOSED fixed-program public records only; no policy or truth access."""
import argparse
import json
from pathlib import Path
import sys

p = argparse.ArgumentParser()
p.add_argument('--source', type=Path, required=True)
p.add_argument('--records', type=Path, required=True)
args = p.parse_args()
sys.path.insert(0, str(args.source))
import numpy as np
from agentic_cap.public_sensors import visible_surface
from agentic_cap.cap_backend import public_cube_center

trials = json.loads((args.records/'results.json').read_text())
for trial in trials:
    if trial['case']['task'] != 'cube_restack' or trial['case']['mode'] != 'support_active':
        continue
    segment = trial['segments'][-1]
    ref = (segment.get('feedback') or {}).get('observation', {}).get('evidence_ref')
    if not ref:
        continue
    if Path(ref).name != ref or not ref.startswith('frame-') or not ref.endswith('.npz'):
        raise ValueError('Invalid public frame reference')
    prior = trial['measurements'][0]['objects']['green cube']['visible']
    with np.load(args.records/trial['episode_id']/ref, allow_pickle=False) as saved:
        packet = {key: saved[key] for key in saved.files}
    obj = visible_surface(packet, 'green cube')
    center, method = public_cube_center(obj, prior) if obj['status'] == 'visible' else (None, 'unobservable')
    print(json.dumps(dict(case=trial['case'], evidence_ref=ref, visible=obj,
                          prior_spans=prior.get('surface_span_xyz'),
                          existing_center_method=method, center=center.tolist() if center is not None else None,
                          depth_quantization_m=float(packet.get('depth_quantization_m', 0)),
                          scope='post-hoc archived public RGB-D only; no policy change')))
