"""Experimental public-cloud occlusion cue; deliberately not a Runtime fact.

Only surfaces visible in the source packet are modeled. The robot, hidden cube
faces, other objects, contacts and geometry changes are omitted, so even a low
predicted obstruction never certifies future visibility or authorizes release.
"""
import copy
import numpy as np
from scipy.spatial.transform import Rotation
from agentic_cap.public_sensors import projected_surface


def finite(value,shape):
    array=np.asarray(value,dtype=float)
    if array.shape!=shape or not np.isfinite(array).all():raise ValueError('Invalid finite geometry')
    return array


def rotation(q):
    q=finite(q,(4,));length=np.linalg.norm(q)
    if length<1e-8:raise ValueError('Invalid quaternion')
    q=q/length
    return Rotation.from_quat(q[[1,2,3,0]])


def predict_obstruction(packet,current_tcp,candidate_tcp,held,support,*,mount):
    result=dict(status='unknown',reason='prediction_unavailable',
        evidence_ref=packet.get('evidence_ref'),control_step=packet.get('control_step'),
        camera_name=packet.get('camera_name'),mount=mount,
        candidate_tcp=copy.deepcopy(candidate_tcp),
        authority='experimental_projection_cue_only; never current evidence or action permission',
        assumptions=['Held visible surface moves rigidly with TCP.',
                     'Support remains stationary; no contact or external disturbance.',
                     'Camera follows its explicitly configured fixed or TCP-mounted relation.'],
        omissions=['Unseen surfaces, gripper, arm and other occluders.',
                   'Physical stability, calibration uncertainty, label errors and future sensing changes.'])
    try:
        if mount not in {'fixed','tcp_mounted'}:raise ValueError('Unknown camera mount')
        if held==support:raise ValueError('Held and support must differ')
        if (packet.get('coordinate_frame')!='cap_robot_base' or packet.get('length_unit')!='m'
            or packet.get('quaternion_order')!='WXYZ'):raise ValueError('Invalid public coordinate convention')
        k=finite(packet['intrinsics'],(3,3));camera=finite(packet['camera_pose_xyz_wxyz'],(7,))
        if k[0,0]<=0 or k[1,1]<=0:raise ValueError('Invalid intrinsics')
        source=finite(current_tcp['xyz'],(3,));target=finite(candidate_tcp['xyz'],(3,))
        delta=rotation(candidate_tcp['wxyz'])*rotation(current_tcp['wxyz']).inv()
        moving,_,count=projected_surface(packet,held)
        base,_,basecount=projected_surface(packet,support)
        if count<40 or basecount<40 or len(moving)<40 or len(base)<40:
            return dict(result,reason='source_surface_missing_or_too_small')
        moved=delta.apply(moving-source)+target
        camera_xyz=camera[:3];camera_rot=rotation(camera[3:])
        if mount=='tcp_mounted':
            camera_xyz=delta.apply(camera_xyz-source)+target
            camera_rot=delta*camera_rot
        def projection(points):
            points=camera_rot.inv().apply(points-camera_xyz)
            z=points[:,2];valid=z>.015
            uv=np.full((len(points),2),np.nan)
            uv[valid]=points[valid,:2]/z[valid,None]*[k[0,0],k[1,1]]+[k[0,2],k[1,2]]
            h,w=np.asarray(packet['rgb']).shape[:2]
            inside=valid&(uv[:,0]>=0)&(uv[:,0]<w)&(uv[:,1]>=0)&(uv[:,1]<h)
            return uv,z,inside
        held_uv,held_z,held_inside=projection(moved)
        support_uv,support_z,support_inside=projection(base)
        if held_inside.sum()<3 or support_inside.sum()<40:
            return dict(result,reason='candidate_projection_outside_sensor_or_insufficient')
        lo,hi=np.quantile(held_uv[held_inside],[.01,.99],axis=0)
        # A screen-space rectangle is a coarse cue, not an occlusion proof.
        overlap=(support_inside & (support_uv[:,0]>=lo[0]) & (support_uv[:,0]<=hi[0])
                 & (support_uv[:,1]>=lo[1]) & (support_uv[:,1]<=hi[1])
                 & (support_z>np.quantile(held_z[held_inside],.05)))
        remaining=base[support_inside&~overlap]
        source_span=np.diff(np.quantile(base,[.05,.95],axis=0),axis=0)[0]
        scale=float(max(source_span));ratio=None
        if len(remaining)>=40 and scale>0:
            span=np.diff(np.quantile(remaining,[.05,.95],axis=0),axis=0)[0]
            ratio=float(min(span[:2])/scale)
        return dict(result,status='predicted',reason='visible_surface_projection_without_robot_occluders',
            source_support_points=len(base),projected_support_points=int(support_inside.sum()),
            predicted_obstructed_fraction=float(overlap.sum()/support_inside.sum()),
            predicted_remaining_points=len(remaining),predicted_remaining_horizontal_ratio=ratio,
            occluder_screen_rectangle=[lo.tolist(),hi.tolist()],
            future_camera_xyz=camera_xyz.tolist(),future_camera_wxyz=camera_rot.as_quat()[[3,0,1,2]].tolist())
    except (ValueError,KeyError,TypeError,IndexError):
        return dict(result,reason='invalid_or_missing_public_projection_inputs')
