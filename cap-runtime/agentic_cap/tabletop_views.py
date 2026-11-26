"""Finite inspection candidates ranked by PUBLIC image coverage, not physical truth.

Predicted projection and missing-depth masks select a view, never verify an effect.
Historical measured geometry is only an aiming prior. All movement still uses S1/S0.
"""
import itertools,time
import numpy as np

def signature(xyz):return tuple(np.round(np.asarray(xyz)/.01).astype(int))

def rank_views(packet,initial,center,span,visited=(),*,holding=False):
    xyz=np.asarray(initial,float);center=np.asarray(center,float);span=np.asarray(span,float)
    cam=packet.get('cameras',{}).get('wrist')
    if cam is None or not np.isfinite(np.r_[xyz,center,span]).all():return []
    try:
        K=np.asarray(cam['intrinsics'],float);T=np.asarray(cam['camera_to_world'],float);depth=np.asarray(cam['depth'])
        if (K.shape!=(3,3) or T.shape!=(4,4) or not np.isfinite(K).all() or not np.isfinite(T).all()
            or min(K[0,0],K[1,1])<=0 or abs(cam['captured_at']-packet['captured_at'])>.05
            or not 0<=time.time()-packet['captured_at']<=1
            or not np.allclose(T[3],[0,0,0,1]) or not np.allclose(T[:3,:3].T@T[:3,:3],np.eye(3),atol=1e-4)
            or abs(np.linalg.det(T[:3,:3])-1)>1e-4):return []
    except (KeyError,TypeError,ValueError):return []
    # Measured 3x3 surface samples; no claim about hidden free space or extent.
    points=np.array([center+[x,y,0] for x in [-span[0]/2,0,span[0]/2] for y in [-span[1]/2,0,span[1]/2]])
    offsets=([np.array([0,dy,0]) for dy in [-.08,.08]] if holding else
        [np.array([dx,dy,dz]) for dz in [0,.06] for dx,dy in itertools.product([-.08,0,.08],repeat=2) if dx or dy or dz])
    h,w=depth.shape;result=[]
    for delta in offsets:
        target=xyz+delta
        if signature(target) in visited or abs(target[0])>.35 or abs(target[1])>.35 or not .95<=target[2]<=1.25:continue
        camera_shift=target-np.asarray(packet.get('tcp_xyz',xyz))
        local=(points-(T[:3,3]+camera_shift))@T[:3,:3]
        valid=local[:,2]>.05;pixels=np.zeros((len(points),2))
        pixels[valid]=local[valid,:2]/local[valid,2,None]@K[:2,:2].T+K[:2,2]
        inside=valid&(pixels[:,0]>=3)&(pixels[:,0]<w-3)&(pixels[:,1]>=3)&(pixels[:,1]<h-3)
        sample=np.round(pixels[inside]).astype(int)
        visible=np.isfinite(depth[sample[:,1],sample[:,0]])&(depth[sample[:,1],sample[:,0]]>.05)
        coverage=float(visible.sum()/len(points));in_frame=float(inside.mean())
        # Reject candidates aimed almost entirely outside FOV; zero valid depth
        # is recorded honestly and does not count as a predicted observation.
        if in_frame<.55:continue
        score=coverage+.2*in_frame-.05*float(np.linalg.norm(delta))
        result.append(dict(target_xyz=target.tolist(),signature=list(signature(target)),score=score,
            predicted_valid_fraction=coverage,predicted_in_frame_fraction=in_frame,
            scope='image_projection_and_current_missing_depth_only; not_visibility_or_collision_proof'))
    return sorted(result,key=lambda row:row['score'],reverse=True)
