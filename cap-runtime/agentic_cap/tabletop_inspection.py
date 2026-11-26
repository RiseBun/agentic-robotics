"""Evidence-directed ordering over an existing candidate set; never action proof.

Only copied public geometry and camera apertures are used. The score predicts
sensor support, not object visibility, collision clearance or task success.
"""
import copy
import time
import numpy as np
from .tabletop_evidence import geometry_problem


def evidence_request(obj,evidence,result,held=None,custody=None):
    requested=[dict(predicate=evidence,object=obj)]
    if held and not (held==obj and evidence=='holding'):requested.append(dict(predicate='holding',object=held))
    target=next((c for c in result.get('conditions',[]) if c.get('condition')==requested[0]),result)
    detail=target.get('evidence_gap',{})
    missing=[int(a) for a in detail.get('missing_axes',[]) if a in [0,1]]
    return dict(target=requested[0],requirements=requested,
        target_status=target.get('status','unknown'),target_reason=target.get('reason',result.get('reason','unknown')),
        missing_axes=missing,extent_detail=copy.deepcopy(detail),custody=copy.deepcopy(custody),
        interpretation='diagnostic_only; does_not_authorize_motion')


def _aperture(packet,name,points,delta):
    """Fraction of projected samples inside current valid-depth pixels."""
    cam=packet.get('cameras',{}).get(name)
    if cam is None:return None
    try:
        K=np.asarray(cam['intrinsics'],float);T=np.asarray(cam['camera_to_world'],float);depth=np.asarray(cam['depth'])
        if (K.shape!=(3,3) or T.shape!=(4,4) or depth.ndim!=2 or not np.isfinite(K).all() or not np.isfinite(T).all()
            or min(K[0,0],K[1,1])<=0 or abs(cam['captured_at']-packet['captured_at'])>.05
            or not np.allclose(T[3],[0,0,0,1]) or not np.allclose(T[:3,:3].T@T[:3,:3],np.eye(3),atol=1e-4)
            or abs(np.linalg.det(T[:3,:3])-1)>1e-4):return None
        shift=delta if name=='wrist' else np.zeros(3)
        local=(points-(T[:3,3]+shift))@T[:3,:3]
        valid=local[:,2]>.05;uv=np.zeros((len(points),2))
        uv[valid]=local[valid,:2]/local[valid,2,None]@K[:2,:2].T+K[:2,2]
        h,w=depth.shape
        inside=valid&(uv[:,0]>=3)&(uv[:,0]<w-3)&(uv[:,1]>=3)&(uv[:,1]<h-3)
        pixels=np.round(uv[inside]).astype(int);values=depth[pixels[:,1],pixels[:,0]]
        return float(np.count_nonzero(np.isfinite(values)&(values>.05))/len(points))
    except (KeyError,TypeError,ValueError,IndexError):return None


def order_evidence_views(candidates,packet,snapshot,reference,request,held=None):
    """Reorder only: missing prerequisites keep the original order explicitly."""
    rows=copy.deepcopy(candidates)
    def fallback(reason):
        for r in rows:r['evidence_prediction']=dict(status='unavailable',reason=reason)
        return rows
    if not 0<=time.time()-packet.get('captured_at',0)<=1:return fallback('current_camera_packet_unavailable')
    target=request['target'];ident=target['object'];ref=reference.get(ident)
    if not ref:return fallback('public_target_size_prior_missing')
    try:
        center=np.asarray(ref['surface_median_xyz'],float);span=np.asarray(ref['surface_span_xyz'],float)
        if center.shape!=(3,) or span.shape!=(3,) or not np.isfinite(np.r_[center,span]).all() or np.any(span[:2]<=0):
            return fallback('invalid_public_target_size_prior')
        # A fixed object may use its measured old region as an aiming prior.
        # A movable target requires its current paired measurement instead.
        from .tabletop_scene import public_catalog
        if public_catalog()['objects'][ident].get('movable',False):
            current=snapshot.get('objects',{}).get(ident,{})
            if current.get('status')!='available' or geometry_problem(current,snapshot):return fallback('current_target_geometry_unavailable')
            if not 0<=packet['captured_at']-current.get('captured_at',0)<=1:return fallback('target_geometry_stale_or_future')
            center=np.asarray(current['surface_median_xyz'],float)
            if center.shape!=(3,) or not np.isfinite(center).all():return fallback('target_position_invalid')
        held_data=None
        if held:
            held_data=snapshot.get('objects',{}).get(held,{})
            if (request.get('custody') or {}).get('status')!='verified' or held_data.get('status')!='available' or geometry_problem(held_data,snapshot):
                return fallback('current_custody_geometry_unavailable')
            if not 0<=packet['captured_at']-held_data.get('captured_at',0)<=1:return fallback('custody_geometry_stale_or_future')
        axes=request.get('missing_axes') or [0,1]
        groups=[]
        for axis in axes:
            other=1-axis
            for side in [-1,1]:
                points=[]
                for coordinate in np.linspace(-.5,.5,9):
                    point=center.copy();point[axis]+=side*.5*span[axis];point[other]+=coordinate*span[other];points.append(point)
                groups.append(np.asarray(points))
        for row in rows:
            delta=np.asarray(row['target_xyz'],float)-np.asarray(packet['tcp_xyz'],float)
            target_shift=delta if ident==held else np.zeros(3)
            edges=[_aperture(packet,'wrist',g+target_shift,delta) for g in groups]
            if any(v is None for v in edges):return fallback('wrist_projection_unavailable')
            target_score=min(edges)
            custody_score=1.;custody_views={}
            if held:
                # Conditioned on current verified custody, translate the observed
                # held surface with the TCP. This is a prediction, never proof.
                hc=np.asarray(held_data['surface_median_xyz'],float)
                hs=np.asarray(held_data['surface_span_xyz'],float)
                if hc.shape!=(3,) or hs.shape!=(3,) or not np.isfinite(np.r_[hc,hs]).all():return fallback('custody_surface_invalid')
                hp=np.array([hc+[x*hs[0],y*hs[1],0] for x in [-.5,0,.5] for y in [-.5,0,.5]])+delta
                custody_views={name:_aperture(packet,name,hp,delta) for name in packet.get('cameras',{})}
                scores=[v for v in custody_views.values() if v is not None]
                if not scores:return fallback('custody_camera_projection_unavailable')
                custody_score=max(scores)
            row['coverage_score']=row['score']
            row['evidence_prediction']=dict(status='predicted',target_edge_fractions=edges,
                target_minimum_edge_fraction=target_score,custody_aperture_fraction=custody_score,
                custody_cameras=custody_views,required_axes=axes,joint_minimum_fraction=min(target_score,custody_score),
                scope='public_aperture_only; ignores_future_occlusion_and_does_not_verify_any_fact')
        return sorted(rows,key=lambda r:(r['evidence_prediction']['joint_minimum_fraction'],
            r['evidence_prediction']['target_minimum_edge_fraction'],r['score']),reverse=True)
    except (KeyError,TypeError,ValueError,IndexError):return fallback('public_projection_input_incomplete')
