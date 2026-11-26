from pathlib import Path
import json,sys,hashlib,time,numpy as np
from scipy.spatial.transform import Rotation
p=Path('/home/li/projects/agentic-robotics');sys.path.insert(0,str(p/'cap-runtime'))
from agentic_cap.tabletop_backend import TabletopBackend
from agentic_cap.tabletop_sensors import project,provider_depth
from cap_harness.contracts import Pose,PlanningScene,PointCloud,CameraObservation
from cap_harness.providers.curobo.client import CuRoboProvider
from cap_harness.providers.sam3.client import Sam3Provider
out=p/'research/recorded_restore_diagnosis_20261126';ep=next((out/'fault202-v1/physical').glob('*/episode.json')).parent
assert json.loads((ep/'episode.json').read_text())['status']=='closed'
es=[json.loads(l) for l in (ep/'events.jsonl').read_text().splitlines()]
saved=next(e for e in es if e['kind']=='recorded_descent_saved');release=next(e for e in es if e['kind']=='release_admission_checked')
plans=[e for e in es if e['kind']=='motion_plan_requested' and e['id']<saved['id']];approach,descent=plans[-2:]
assert abs(approach['target_xyz'][2]-descent['target_xyz'][2]-.13)<1e-6
step=release['result']['geometry_evidence_source']['control_step'];base=np.array(next(e for e in es if e['kind']=='robot_mount_calibrated')['base_transform'])
backend=object.__new__(TabletopBackend);backend.base_T=base
rows=[];inputs=[]
for camera,stem in [('overview','frame'),('wrist','view-robot0_eye_in_hand')]:
 file=ep/f'{stem}-{step:06d}.npz'
 with np.load(file,allow_pickle=False) as z:
  cam={k:z[k].copy() for k in ['rgb','depth','intrinsics','camera_to_world']};cam['captured_at']=float(z['camera_captured_at'])
  snapshot={k:z[k].tolist() for k in ['joint_positions','tcp_xyz','tcp_wxyz','last_gripper_command','captured_at','control_step']}
 state,context=backend._typed_state(snapshot);T=cam['camera_to_world'];q=Rotation.from_matrix(T[:3,:3]).as_quat()[[3,0,1,2]]
 variants=[('uncarved',None)]
 if camera=='wrist':
  obs=CameraObservation(cam['rgb'],provider_depth(cam['depth']),cam['intrinsics'],camera,Pose(T[:3,3],q,'world'),cam['captured_at'])
  segmented=Sam3Provider('http://127.0.0.1:8114',timeout_s=30,max_retries=0).segment_text(obs,'blue block')
  if segmented.ok and segmented.segmentations:
   mask=np.asarray(max(segmented.segmentations,key=lambda s:s.score).mask,bool)
   variants.append(('fresh_sam_held_mask_reconstruction',~mask))
 for variant,mask in variants:
  points,_=project(cam,mask);points=points[(points[:,2]>.75)&(points[:,2]<1.8)];points=points[::max(1,len(points)//15000)]-base[:3,3]
  pose=Pose(np.array(approach['target_xyz'])-base[:3,3],np.array(approach['target_wxyz']),'robot_base')
  start=time.monotonic();result=CuRoboProvider(timeout_s=60,max_retries=0,control_period_s=.05).plan_to_pose(state,pose,scene=PlanningScene(PointCloud(points,'robot_base')),context=context)
  row=dict(camera=camera,variant=variant,points=len(points),ok=bool(result.ok),error=str(result.error) if not result.ok else None,
   collision_aware=bool(result.trajectory.collision_aware) if result.ok and result.trajectory is not None else None,
   waypoints=len(result.trajectory.joint_positions) if result.ok and result.trajectory is not None else 0,
   wall_seconds=time.monotonic()-start,executed=False)
  rows.append(row);print('PLAN',json.dumps(row),flush=True)
 inputs.append(dict(camera=camera,source_file=str(file.relative_to(p)),source_sha256=hashlib.sha256(file.read_bytes()).hexdigest(),captured_at=snapshot['captured_at'],control_step=snapshot['control_step'],start_joints=snapshot['joint_positions']))
(out/'frozen-scene-planning.json').write_text(json.dumps(dict(scope='offline archived public scene feasibility only; no physical execution or current-live certificate',inputs=inputs,target_xyz=approach['target_xyz'],target_wxyz=approach['target_wxyz'],results=rows,limitations=['no attached-object collision model','fresh SAM mask is reconstructed, not exact original segmentation','missing image depth remains unobserved; no free-space certificate']),indent=2))
