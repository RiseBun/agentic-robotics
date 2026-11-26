from pathlib import Path
import sys,json,numpy as np,cv2
from scipy.spatial.transform import Rotation
p=Path('/home/li/projects/agentic-robotics');sys.path.insert(0,str(p/'cap-runtime'))
from cap_harness.contracts import CameraObservation,Pose
from cap_harness.providers.sam3.client import Sam3Provider
from agentic_cap.tabletop_sensors import provider_depth,project
from agentic_cap.tabletop_multiview import fixed_reference_mask,refresh_identity,track_mask
from agentic_cap.tabletop_scene import public_catalog
out=p/'research/held_inspection_envelope_20261126';ep=next((out/'fault202-evidence/physical').glob('*/episode.json')).parent
es=[json.loads(l) for l in (ep/'events.jsonl').read_text().splitlines()];selection=next(e for e in es if e['kind']=='inspection_selection_inputs');center=np.array(selection['aim_xyz']);span=np.array(selection['measured_span']);ref=dict(surface_q02_xyz=(center-span/2).tolist(),surface_q98_xyz=(center+span/2).tolist())
initial_step=next(k for k in sorted(int(f.stem.split('-')[-1]) for f in ep.glob('frame-*.npz')) if k>=300)
with np.load(ep/f'frame-{initial_step:06d}.npz',allow_pickle=False) as z:
 initial={k:z[k].copy() for k in ['rgb','depth','intrinsics','camera_to_world']};initial['captured_at']=float(z['camera_captured_at'])
T=initial['camera_to_world'];q=Rotation.from_matrix(T[:3,:3]).as_quat()[[3,0,1,2]]
initial_obs=CameraObservation(initial['rgb'],provider_depth(initial['depth']),initial['intrinsics'],'overview',Pose(T[:3,3],q,'world'),initial['captured_at'])
identities={};provider=Sam3Provider('http://127.0.0.1:8114',timeout_s=30,max_retries=0)
for ident,spec in public_catalog()['objects'].items():
 if ident!='table':refresh_identity(initial,ident,identities,provider.segment_text(initial_obs,spec['label']))
assert 'platform' in identities
releases=[e for e in es if e['kind']=='release_admission_checked'];rows=[]
for release in releases:
 step=release['result']['geometry_evidence_source']['control_step'];file=ep/f'view-robot0_eye_in_hand-{step:06d}.npz'
 with np.load(file,allow_pickle=False) as z:cam={k:z[k].copy() for k in ['rgb','depth','intrinsics','camera_to_world']};cam['captured_at']=float(z['camera_captured_at'])
 T=cam['camera_to_world'];q=Rotation.from_matrix(T[:3,:3]).as_quat()[[3,0,1,2]]
 obs=CameraObservation(cam['rgb'],provider_depth(cam['depth']),cam['intrinsics'],'wrist',Pose(T[:3,3],q,'world'),cam['captured_at'])
 mask=track_mask(cam,'platform',identities,center,False,ref)
 count,labels,stats,_=cv2.connectedComponentsWithStats(mask.astype(np.uint8),8)
 if count<2:rows.append(dict(step=step,status='no_current_component'));continue
 mask=labels==1+int(np.argmax(stats[1:,cv2.CC_STAT_AREA]))
 depth=cam['depth'];yy,xx=np.indices(depth.shape);K=cam['intrinsics'];local=np.stack([(xx-K[0,2])*depth/K[0,0],(yy-K[1,2])*depth/K[1,1],depth],axis=-1);world=local@T[:3,:3].T+T[:3,3]
 topz=float(np.median(world[:,:,2][mask]));top=mask&(abs(world[:,:,2]-topz)<.003);valid=np.isfinite(depth)&(depth>.05)&(depth<3)
 points=[];neighbors=[];pixels=[]
 h,w=depth.shape
 for y,x in zip(*np.nonzero(top)):
  for dy,dx in [(0,1),(0,-1),(1,0),(-1,0)]:
   ny,nx=y+dy,x+dx
   if not 0<=ny<h or not 0<=nx<w or not valid[ny,nx]:continue
   if abs(world[ny,nx,2]-.8)>.006 or topz-world[ny,nx,2]<.012:continue
   points.append(world[y,x]);neighbors.append(world[ny,nx]);pixels.append([y,x])
 points=np.asarray(points);neighbors=np.asarray(neighbors);hypotheses=[]
 if len(points):
  for axis in [0,1]:
   bins=np.round(points[:,axis]/.002).astype(int)
   for b in sorted(set(bins),key=lambda v:np.count_nonzero(bins==v),reverse=True)[:4]:
    use=abs(points[:,axis]-b*.002)<.002;sample=points[use];outside=neighbors[use];unique=len(set(tuple(x) for x in np.asarray(pixels)[use]))
    if unique<24 or np.ptp(sample[:,1-axis])<.015:continue
    bound=float(np.median(sample[:,axis]));residual=float(np.max(abs(sample[:,axis]-bound)));delta=float(np.median(outside[:,axis]-sample[:,axis]))
    hypotheses.append(dict(axis=axis,coordinate=bound,unique_pixels=unique,line_span_m=float(np.ptp(sample[:,1-axis])),maximum_axis_residual_m=residual,outward_delta_m=delta,prior_width_m=float(span[axis])))
 rows.append(dict(step=step,status='diagnosed',source=str(file.relative_to(p)),top_z=topz,natural_table_edge_pairs=len(points),hypotheses=hypotheses))
 print('EDGE',json.dumps(rows[-1]),flush=True)
(out/'natural-edge-diagnosis.json').write_text(json.dumps(dict(scope='offline exploratory fresh initial SAM identity and current color tracking; valid top-to-calibrated-table neighbors only; no verifier modification or release permission',rows=rows),indent=2))
