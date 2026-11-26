from pathlib import Path
import json,sys,time,numpy as np
from unittest.mock import patch
from scipy.spatial.transform import Rotation
p=Path('/home/li/projects/agentic-robotics');sys.path.insert(0,str(p/'cap-runtime'))
from agentic_cap.tabletop_views import rank_views
from agentic_cap.tabletop_inspection import order_evidence_views
from agentic_cap.tabletop_evidence import stamp_packet,source_for
from agentic_cap.tabletop_sensors import provider_depth
from agentic_cap.tabletop_multiview import measure_single_view,refresh_identity
from cap_harness.contracts import Pose,CameraObservation
from cap_harness.providers.sam3.client import Sam3Provider
out=p/'research/held_inspection_envelope_20261126';ep=next((p/'research/replanned_retreat_20261126/fault202/physical').glob('*/episode.json')).parent
es=[json.loads(l) for l in (ep/'events.jsonl').read_text().splitlines()]
selection=next(e for e in es if e['kind']=='inspection_selection_inputs');ranked=next(e for e in es if e['kind']=='inspection_candidates_ranked');request=next(e for e in es if e['kind']=='inspection_evidence_request')['request']
match=None
for f in ep.glob('frame-*.npz'):
 with np.load(f,allow_pickle=False) as z:
  if abs(float(z['captured_at'])-selection['source_captured_at'])<1e-6:match=int(z['control_step']);break
assert match is not None
packet=None;cams={}
for name,stem in [('overview','frame'),('wrist','view-robot0_eye_in_hand')]:
 with np.load(ep/f'{stem}-{match:06d}.npz',allow_pickle=False) as z:
  packet={k:z[k].tolist() for k in ['joint_positions','tcp_xyz','tcp_wxyz','gripper_joint_positions','last_gripper_command','captured_at','control_step','episode_id']}
  cams[name]={k:z[k].copy() for k in ['rgb','depth','intrinsics','camera_to_world']};cams[name]['captured_at']=float(z['camera_captured_at'])
packet['cameras']=cams;stamp_packet(packet,packet['episode_id'])
cam=cams['overview'];T=cam['camera_to_world'];q=Rotation.from_matrix(T[:3,:3]).as_quat()[[3,0,1,2]]
obs=CameraObservation(cam['rgb'],provider_depth(cam['depth']),cam['intrinsics'],'overview',Pose(T[:3,3],q,'world'),cam['captured_at'])
identities={};seg=Sam3Provider('http://127.0.0.1:8114',timeout_s=30,max_retries=0).segment_text(obs,'blue block');mask=refresh_identity(cam,'block_a',identities,seg)
held=measure_single_view(packet,'block_a',identities,movable=True,initial_mask=mask)
assert held['status']=='available'
held['evidence_source']=source_for(packet,held['contributing_cameras'])
snapshot=dict(packet,objects={'block_a':held},geometry_evidence_schema=1,geometry_robot_state=packet,geometry_not_before_step=0)
reference={'platform':dict(surface_median_xyz=selection['aim_xyz'],surface_span_xyz=selection['measured_span'])}
rows=[]
with patch('time.time',return_value=packet['captured_at']+.01):
 for expanded in [False,True]:
  candidates=rank_views(packet,ranked['anchor_xyz'],selection['aim_xyz'],selection['measured_span'],holding=True,expanded_holding=expanded)
  ordered=order_evidence_views(candidates,packet,snapshot,reference,request,'block_a')
  row=dict(expanded=expanded,candidates=ordered)
  rows.append(row)
  print('FAMILY','expanded' if expanded else 'original','count',len(ordered),flush=True)
  for c in ordered[:5]:print('TOP',c['target_xyz'],c.get('evidence_prediction'),flush=True)
(out/'archived-candidates.json').write_text(json.dumps(dict(scope='archived public-frame counterfactual projection only; reconstructed held identity; no trajectory or visibility proof',source_episode=ep.name,source_control_step=match,source_captured_at=packet['captured_at'],selection=selection,ranked_anchor=ranked['anchor_xyz'],families=rows),indent=2,default=lambda v:v.item() if isinstance(v,np.generic) else v.tolist()))
