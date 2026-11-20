"""Closed public-frame inspection only; no private evaluator inputs."""
import argparse
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pyroki
from robot_descriptions.loaders.yourdfpy import load_robot_description
from agentic_cap.public_sensors import PublicExecutionMonitor
from agentic_cap.safe_retreat import descent_anchor, aligned

p=argparse.ArgumentParser()
p.add_argument('episode', type=Path)
args=p.parse_args()
events=[json.loads(x) for x in (args.episode/'events.jsonl').read_text().splitlines()]
assert events[-1]['kind']=='episode_closed'
start=next(e for e in events if e['kind']=='segment_started' and e['segment']['subgoal']=='vertical-descent')
gate=next(e for e in events if e['kind']=='placement_motion_checked' and e['segment_id']==start['segment_id'])
call=next(e for e in events if e['kind']=='api_started' and e.get('name')=='goto_pose' and e['segment_id']==start['segment_id'])
with np.load(args.episode/gate['evidence']['evidence_ref'], allow_pickle=False) as packet:
    monitor=PublicExecutionMonitor(pyroki.Robot.from_urdf(load_robot_description('panda_description')), None)
    monitor.last_gripper_command='closed' if packet['gripper_command']<.5 else 'open'
    monitor.step_reader=lambda: int(packet['control_step'])
    state=monitor.state(packet)
state['evidence_ref']=gate['evidence']['evidence_ref']
goal=events[0]['goal']
b=SimpleNamespace(episode=SimpleNamespace(goal=goal),holding_results={'red cube':'verified'})
target,q=call['args'][:2]
print(json.dumps(dict(current_tcp=state['tcp_xyz'], target=target,
                      xy_distance=float(np.linalg.norm(np.array(state['tcp_xyz'][:2])-target[:2])),
                      drop=state['tcp_xyz'][2]-target[2],
                      actual_quaternion=state['tcp_wxyz'],target_quaternion=q,
                      orientation_aligned=aligned(state['tcp_wxyz'],q),
                      last_gripper_command=state['last_gripper_command'],
                      anchor=descent_anchor(b,state,target,q)),indent=2))
