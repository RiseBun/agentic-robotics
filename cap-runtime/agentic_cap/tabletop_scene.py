"""Declarative scene assets and public semantic catalog, never object poses."""
import copy
from functools import lru_cache
from pathlib import Path
import hashlib
import os

SCENES={'tabletop_v1','tabletop_stacked_v1'}
OBJECTS={
 'block_a':dict(label='blue block',shape='box',movable=True,rgba=[.1,.3,.9,1.]),
 'block_b':dict(label='yellow rectangular block',shape='box',movable=True,rgba=[.9,.75,.1,1.]),
 'cylinder_a':dict(label='purple cylinder',shape='cylinder',movable=True,rgba=[.65,.15,.8,1.]),
 'platform':dict(label='flat grey support',shape='box',movable=False,rgba=[.5,.5,.5,1.]),
 'table':dict(label='tabletop',shape='plane',movable=False),
}
REGIONS={
 'left':dict(label='left marked tabletop region',frame='world',bounds_xy=[[-.2,-.25],[-.04,-.08]],surface_z=.8),
 'right':dict(label='right marked tabletop region',frame='world',bounds_xy=[[-.2,.08],[-.04,.25]],surface_z=.8),
}

def public_catalog():
    return dict(objects={k:{n:v for n,v in d.items() if n in {'label','shape','movable'}} for k,d in OBJECTS.items()},
                regions=copy.deepcopy(REGIONS))


@lru_cache(maxsize=1)
def runtime_contract():
    names=['core.py','interpreter.py','robot_system.py','tabletop_domain.py','tabletop_system.py','tabletop_episode.py','tabletop_scene.py','tabletop_sensors.py','tabletop_actions.py','tabletop_backend.py','tabletop_simulation.py','tabletop_multiview.py','tabletop_evidence.py','tabletop_recovery.py','tabletop_views.py']
    return hashlib.sha256(('perception_multiview='+os.environ.get('TABLETOP_MULTIVIEW','0')+'\ninspection_policy='+os.environ.get('TABLETOP_INSPECTION_POLICY','lateral')+'\n'+''.join(n+'\0'+hashlib.sha256((Path(__file__).parent/n).read_bytes()).hexdigest() for n in names)).encode()).hexdigest()


def system_description():
    return dict(schema=2,runtime_contract=runtime_contract(),role='simulation',robot=dict(id='panda_tabletop',arms=['primary'],joint_count=7,
        gripper='parallel_jaw',grasp_frame_to_tcp_m=[0.,0.,.12],frame='world',position_units='meters',quaternion='wxyz'),
        sensors=[dict(id='agentview',alias='overview',kind='rgbd',mount='fixed',calibration_required=True),
                 dict(id='robot0_eye_in_hand',alias='wrist',kind='rgbd',mount='end_effector',calibration_required=True)],
        supported_predicates=['holding','on','in_region','empty_gripper'],
        control=dict(owner='Runtime physics loop',stop='hold measured joints and preserve gripper; confirm before reporting'),
        catalog=public_catalog())
