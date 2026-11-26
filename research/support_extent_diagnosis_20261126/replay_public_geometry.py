from pathlib import Path
import json,sys,numpy as np
from scipy.spatial.transform import Rotation
from PIL import Image,ImageDraw
p=Path('/home/li/projects/agentic-robotics');sys.path.insert(0,str(p/'cap-runtime'))
from cap_harness.contracts import CameraObservation,Pose
from cap_harness.providers.sam3.client import Sam3Provider
from agentic_cap.tabletop_scene import public_catalog
from agentic_cap.tabletop_multiview import refresh_identity,measure_single_view,measure_object,track_mask
from agentic_cap.tabletop_sensors import provider_depth,support_surface
out=p/'research/support_extent_diagnosis_20261126';ep=next((p/'research/joint_inspection_evidence_20261126/support-201/physical').glob('*/episode.json')).parent

def packet(step):
 cams={};data=None
 for name,stem in [('overview','frame'),('wrist','view-robot0_eye_in_hand')]:
  with np.load(ep/f'{stem}-{step:06d}.npz',allow_pickle=False) as z:
   data={k:z[k].tolist() for k in ['joint_positions','tcp_xyz','tcp_wxyz','gripper_joint_positions','last_gripper_command','captured_at','control_step','episode_id']}
   cams[name]={k:z[k].copy() for k in ['rgb','depth','intrinsics','camera_to_world']};cams[name]['captured_at']=float(z['camera_captured_at'])
 return dict(data,cameras=cams)
steps=sorted(int(f.stem.split('-')[-1]) for f in ep.glob('frame-*.npz'))
first=next(s for s in steps if s>=300);initial=packet(first);cam=initial['cameras']['overview'];T=cam['camera_to_world'];q=Rotation.from_matrix(T[:3,:3]).as_quat()[[3,0,1,2]]
obs=CameraObservation(cam['rgb'],provider_depth(cam['depth']),cam['intrinsics'],'overview',Pose(T[:3,3],q,'world'),cam['captured_at'])
provider=Sam3Provider('http://127.0.0.1:8114',timeout_s=30,max_retries=0);identities={};masks={}
for ident,d in public_catalog()['objects'].items():
 if ident=='table':continue
 masks[ident]=refresh_identity(cam,ident,identities,provider.segment_text(obs,d['label']))
 print('IDENTITY',ident,ident in identities,flush=True)
reference=measure_single_view(initial,'platform',identities,movable=False,initial_mask=masks['platform'])
reference={k:v for k,v in reference.items() if k!='mask'};reference['observed_height_m']=reference['surface_q98_xyz'][2]-.8
prior=reference['surface_median_xyz'];rows=[];panels=[]
for step in [first,1290,1583,1902,2009]:
 pack=packet(step);views={};images=[]
 for name in ['overview','wrist']:
  value=measure_object(dict(pack,cameras={name:pack['cameras'][name]}),'platform',identities,prior,reference,False)
  mask=track_mask(pack['cameras'][name],'platform',identities,prior,False,reference)
  compact={k:v for k,v in value.items() if k!='mask'}
  if value['status']=='available':
   compact['span_ratio_xy']=(np.array(value['surface_span_xyz'][:2])/reference['surface_span_xyz'][:2]).tolist();compact['support_extent_established']=support_surface(value,reference) is not None
  views[name]=compact
  img=Image.fromarray(pack['cameras'][name]['rgb']);overlay=np.asarray(img).copy();overlay[mask]=[255,0,0];img=Image.blend(img,Image.fromarray(overlay),.5);ImageDraw.Draw(img).text((4,4),f'{step} {name}',fill='white');images.append(img)
 selected=measure_single_view(pack,'platform',identities,prior,False,reference=reference)
 union=measure_object(pack,'platform',identities,prior,reference,False)
 rows.append(dict(step=step,cameras=views,selected=selected.get('camera'),union_available=union['status'],union_support_extent_established=support_surface(union,reference) is not None if union['status']=='available' else False))
 panel=Image.new('RGB',(640,240));panel.paste(images[0],(0,0));panel.paste(images[1],(320,0));panels.append(panel)
 print('MEASURE',step,{n:{k:v.get(k) for k in ['status','surface_span_xyz','span_ratio_xy','support_extent_established','plane_normal_xyz','plane_residual_m','point_count']} for n,v in views.items()},'selected',selected.get('camera'),'union_support',rows[-1]['union_support_extent_established'],flush=True)
montage=Image.new('RGB',(640,240*len(panels)))
for i,img in enumerate(panels):montage.paste(img,(0,240*i))
montage.save(out/'replay-masks.png')
(out/'offline-replay.json').write_text(json.dumps(dict(scope='offline reconstruction with new SAM identity on archived public RGB-D; not exact live identities or independent success evaluation',initial_step=first,reference=reference,identities={k:{n:np.asarray(v).tolist() for n,v in d.items()} for k,d in identities.items()},rows=rows),indent=2))
