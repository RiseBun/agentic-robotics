"""CPU-only replay of upstream IK with public encoder inputs; no simulator."""
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np
import jax.numpy as jnp
import jaxlie
from scipy.spatial.transform import Rotation
from capx.serving import launch_pyroki_server as service

root = Path(sys.argv[1]).resolve()
inputs = json.loads((root / 'inputs.json').read_text())
service.init_pyroki_server()
robot = service._ROBOT
index = robot.links.names.index('panda_hand')

def fk(joints):
    hand = jaxlie.SE3(robot.forward_kinematics(jnp.array(joints))[index])
    q = np.asarray(hand.rotation().wxyz)
    p = np.asarray(hand.translation()) - Rotation.from_quat(q[[1, 2, 3, 0]]).apply([0, 0, -.107])
    return p, q

def measure(joints, target, quaternion):
    p, q = fk(joints)
    angle = (Rotation.from_quat(q[[1, 2, 3, 0]]) *
             Rotation.from_quat(np.array(quaternion)[[1, 2, 3, 0]]).inv()).magnitude()
    return dict(tcp_xyz=p.tolist(), tcp_wxyz=q.tolist(),
                position_error_m=float(np.linalg.norm(p-target)), orientation_error_rad=float(angle))

results = []
for case in inputs['cases']:
    p = np.array(case['target_xyz'])
    q = np.array(case['target_wxyz'])
    target = np.concatenate([q, p + Rotation.from_quat(q[[1, 2, 3, 0]]).apply([0, 0, -.107])])
    initial = np.concatenate([case['initial_joints'][:7], [0.]])
    prior = initial
    rounds = []
    for i in range(inputs['protocol']['refinement_attempts']):
        start = time.monotonic()
        solved = np.array(service._do_solve_ik(target, prior), dtype=np.float32)
        rounds.append(dict(attempt=i+1, joints=solved.tolist(),
            elapsed_seconds=time.monotonic()-start, **measure(solved, p, q)))
        prior = solved
    pose_only = np.array(service._do_solve_ik(target, None), dtype=np.float32)
    results.append(dict(event_id=case['event_id'], velocity_cost_rounds=rounds,
        pose_only=dict(joints=pose_only.tolist(), **measure(pose_only, p, q)),
        historical_measured=measure(np.concatenate([case['historical_measured_joints'][:7], [0.]]), p, q),
        first_solved_to_historical_joint_error_rad=float(np.linalg.norm(
            np.array(case['historical_measured_joints'])[:7]-np.array(rounds[0]['joints'])[:7]))))
    print(json.dumps(dict(event_id=case['event_id'], errors_m=[r['position_error_m'] for r in rounds],
                          pose_only_error_m=results[-1]['pose_only']['position_error_m'])), flush=True)
upstream = Path(service.__file__).resolve().parents[1]
sources = [Path(service.__file__), upstream / 'integrations/motion/pyroki_snippets/_solve_ik_vel_cost.py',
           upstream / 'integrations/motion/pyroki_snippets/_solve_ik.py']
output = dict(cases=results, scope=inputs['protocol']['scope'],
    replay_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    input_sha256=hashlib.sha256((root/'inputs.json').read_bytes()).hexdigest(),
    sources_sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources})
(root / 'result.json').write_text(json.dumps(output, indent=2), encoding='utf-8')
