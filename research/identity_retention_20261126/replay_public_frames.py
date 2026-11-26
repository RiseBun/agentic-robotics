"""Same historical development RGB-D replay; semantic identity from public SAM3 only."""
import json,sys
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation
from cap_harness.contracts import CameraObservation,Pose
from cap_harness.providers.sam3.client import Sam3Provider
from agentic_cap.tabletop_scene import public_catalog
from agentic_cap.tabletop_sensors import provider_depth
from agentic_cap.tabletop_multiview import refresh_identity,measure_single_view

p=Path('/home/li/projects/agentic-robotics');out=Path(__file__).parent
folder=next((p/'research/rgbd_occlusion_20261126/support-201-v3/physical').glob('*/episode.json')).parent

def camera(step,view):
    name=f'frame-{step:06d}.npz' if view=='overview' else f'view-robot0_eye_in_hand-{step:06d}.npz'
    with np.load(folder/name,allow_pickle=False) as z:
        result={k:z[k] for k in ['rgb','depth','intrinsics','camera_to_world']};result['captured_at']=float(z['camera_captured_at']);result['name']=view
        return result,float(z['captured_at'])
def typed(cam):
    T=cam['camera_to_world'];q=Rotation.from_matrix(T[:3,:3]).as_quat()[[3,0,1,2]]
    return CameraObservation(cam['rgb'],provider_depth(cam['depth']),cam['intrinsics'],'overview',Pose(T[:3,3],q,'world'),cam['captured_at'])
provider=Sam3Provider('http://127.0.0.1:8114',timeout_s=15,max_retries=0)
initial,_=camera(411,'overview');ids={}
for ident,row in public_catalog()['objects'].items():
    if ident=='table':continue
    result=provider.segment_text(typed(initial),row['label']);refresh_identity(initial,ident,ids,result)
assert 'block_a' in ids
oldstamp=ids['block_a']['identity_observed_at'];rows=[]
for step in [1793,1853,1918,2246]:
    cams={name:camera(step,name)[0] for name in ['overview','wrist']};stamp=camera(step,'overview')[1]
    semantic=provider.segment_text(typed(cams['overview']),'blue block')
    committed=refresh_identity(cams['overview'],'block_a',ids,semantic)
    result=measure_single_view(dict(cameras=cams,captured_at=stamp),'block_a',ids)
    rows.append(dict(control_step=step,overview_refresh_committed=committed is not None,
        old_identity_time_preserved=ids['block_a']['identity_observed_at']==oldstamp,
        status=result['status'],camera=result.get('camera'),point_count=result.get('point_count'),
        source_frames=[f'frame-{step:06d}.npz',f'view-robot0_eye_in_hand-{step:06d}.npz'],
        scope='current visible surface only; not task completion'))
(out/'pixel-replay.json').write_text(json.dumps(dict(initial_semantic_frame='frame-000411.npz',rows=rows,private_state_used=False,role='development historical replay, no frozen-test seeds'),indent=2))
print(json.dumps(rows))
