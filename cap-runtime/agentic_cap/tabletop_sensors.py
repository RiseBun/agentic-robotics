"""Public RGB-D geometry and conservative tabletop verification.

Thresholds are development defaults, not yet frozen or a physical benchmark claim.
No simulator handles or object ground truth are accepted by this module.
"""
import copy
import time
import numpy as np
from scipy.spatial.transform import Rotation
from .tabletop_scene import public_catalog
from .tabletop_evidence import geometry_problem, placement_window

THRESHOLDS=dict(min_pixels=24,max_age_seconds=1.,window_seconds=.6,minimum_samples=3,
                lift_m=.06,tcp_distance_m=.09,relative_drift_m=.015,support_gap_m=.018,region_boundary_uncertainty_m=.01)


def provider_depth(depth):
    """Encode invalid sensor pixels as no-return zero for finite-only providers.

    Raw packets keep their original missing values; neither encoding creates
    a valid 3D sample because project() rejects depth <= 0.05.
    """
    values=np.asarray(depth)
    return np.where(np.isfinite(values)&(values>0),values,0.)


def project(camera,mask=None):
    depth=np.asarray(camera['depth']);rgb=np.asarray(camera['rgb']);K=np.asarray(camera['intrinsics'])
    valid=np.isfinite(depth)&(depth>.05)&(depth<3.)
    if mask is not None:valid &= np.asarray(mask,dtype=bool)
    y,x=np.nonzero(valid);z=depth[valid]
    pts=np.stack([(x-K[0,2])*z/K[0,0],(y-K[1,2])*z/K[1,1],z],axis=1)
    T=np.asarray(camera['camera_to_world']);pts=pts@T[:3,:3].T+T[:3,3]
    return pts,rgb[valid]


def geometry(camera,mask):
    mask=np.asarray(mask,dtype=bool);pts,_=project(camera,mask)
    if len(pts)<THRESHOLDS['min_pixels']:return dict(status='unknown',reason='insufficient_segment_depth')
    y,x=np.nonzero(mask);h,w=mask.shape
    if x.min()<2 or y.min()<2 or x.max()>w-3 or y.max()>h-3:
        return dict(status='unknown',reason='segment_truncated_at_image_boundary')
    lo,hi=np.quantile(pts,[.02,.98],axis=0)
    values,vectors=np.linalg.eigh(np.cov(pts.T));normal=vectors[:,0]
    residual=float(np.sqrt(max(0,values[0])))
    return dict(plane_normal_xyz=normal.tolist(),plane_residual_m=residual,
        camera_center_world=np.asarray(camera['camera_to_world'])[:3,3].tolist(),status='available',surface_min_xyz=pts.min(axis=0).tolist(),surface_max_xyz=pts.max(axis=0).tolist(),surface_q02_xyz=lo.tolist(),surface_q98_xyz=hi.tolist(),
        surface_median_xyz=np.median(pts,axis=0).tolist(),surface_span_xyz=(hi-lo).tolist(),
        point_count=len(pts),scope='visible_surface_only',captured_at=camera['captured_at'])


def support_extent_assessment(current,reference):
    """Explain the same conservative completion used by the public verifier."""
    def gap(reason,**details):return dict(status='unknown',reason=reason,**details)
    if not current:return gap('current_support_surface_missing')
    if not reference:return gap('public_support_reference_missing')
    lo=np.array(current['surface_q02_xyz'],float);hi=np.array(current['surface_q98_xyz'],float)
    expected=np.array(reference['surface_span_xyz'],float)
    missing=np.flatnonzero((hi[:2]-lo[:2])<.65*expected[:2])
    metrics=dict(missing_axes=missing.tolist(),span_fraction_xy=np.divide(hi[:2]-lo[:2],expected[:2],
        out=np.zeros(2),where=expected[:2]>0).tolist())
    if len(missing)>1:return gap('two_horizontal_extents_missing',**metrics)
    inferred=[]
    if len(missing)==1:
        axis=int(missing[0]);normal=np.array(current.get('plane_normal_xyz',[0,0,0]))
        if abs(normal[axis])<.95 or current.get('plane_residual_m',1)>.003:
            return gap('missing_extent_requires_current_planar_side',**metrics)
        camera=np.array(current.get('camera_center_world',[0,0,0]))
        face=float(current['surface_median_xyz'][axis]);width=float(expected[axis])
        if width<=.015:return gap('public_side_width_unavailable',**metrics)
        if camera[axis]>face:lo[axis],hi[axis]=face-width,face
        else:lo[axis],hi[axis]=face,face+width
        inferred.append(axis)
    height=reference.get('observed_height_m')
    if height and hi[2]-lo[2]<.65*height and abs(np.array(current.get('plane_normal_xyz',[0,0,0]))[2])<.8:
        if abs(lo[2]-.8)>.012:return gap('current_lower_edge_required_for_support_height',**metrics)
        hi[2]=.8+height;inferred.append(2)
    result=copy.deepcopy(current)
    result.update(surface_q02_xyz=lo.tolist(),surface_q98_xyz=hi.tolist(),
        completion=dict(axes=inferred,method='current_planar_side_plus_public_observed_size',
                        reference_captured_at=reference.get('captured_at'),reference_evidence_source=reference.get('evidence_source'),uncertainty_m=.01))
    return dict(status='available',reason='current_surface_supports_extent',surface=result,**metrics)


def support_surface(current,reference):
    """Complete measured geometry under the unchanged public evidence rules."""
    return support_extent_assessment(current,reference).get('surface')


def verify(goal,snapshot,history,reference,custody=None):
    now=time.time();cat=public_catalog();objects=snapshot.get('objects',{})
    def unknown(reason):return dict(status='unknown',reason=reason)
    def known(ok,reason):return dict(status='verified' if ok else 'failed',reason=reason)
    if now-snapshot.get('captured_at',0)>THRESHOLDS['max_age_seconds']:return unknown('stale_public_observation')
    def visible(obj):
        o=objects.get(obj,{})
        if o.get('status')!='available' or now-o.get('captured_at',0)>1 or geometry_problem(o,snapshot):return None
        return o
    recent=[h for h in history if h['control_step']<=snapshot['control_step'] and h['captured_at']>=snapshot['captured_at']-.9]
    temporal=len(recent)>=THRESHOLDS['minimum_samples'] and recent[-1]['captured_at']-recent[0]['captured_at']>=THRESHOLDS['window_seconds']
    def check(c):
        pred=c['predicate'];obj=c.get('object');o=visible(obj)
        if pred=='region':return known(c['region'] in cat['regions'],'calibrated_static_region')
        if pred=='visible':return known(True,'current_rgbd_segment') if o else unknown('object_not_observed')
        if pred=='geometry':
            ref=reference.get(obj)
            return known(True,'current_rgbd_surface_geometry') if o else unknown('extent_reference_unavailable')
        if pred=='support_geometry':
            if not o:return unknown('support_not_observed')
            reconstructed=support_surface(o,reference.get(obj)) if cat['objects'][obj]['shape']=='box' else o
            result=known(True,'current_support_measurement_with_public_size_prior') if reconstructed is not None else unknown('support_extent_not_established_from_current_surface')
            if cat['objects'][obj]['shape']=='box':
                detail=support_extent_assessment(o,reference.get(obj))
                result['evidence_gap']={k:v for k,v in detail.items() if k!='surface'}
            return result
        if pred=='accessible':
            if not o:return unknown('target_geometry_unavailable_for_access')
            if not temporal:return unknown('accessibility_requires_public_temporal_window')
            blockers=[];unobserved=[]
            for other,d in cat['objects'].items():
                if other!=obj and d.get('movable',False) and visible(other) is None:unobserved.append(other)
                if other==obj or not d.get('movable',False):continue
                relation=check(dict(predicate='on',object=other,support=obj))
                if relation['status']=='verified':blockers.append(other)
            if not blockers and unobserved:return dict(status='unknown',reason='catalog_objects_unobserved_in_access_check',objects=unobserved)
            result=known(not blockers,'observed_top_grasp_blockers' if blockers else 'no_observed_top_grasp_blocker')
            result.update(blockers=blockers,scope='known_catalog_upright_top_grasp; not_collision_certification')
            return result
        if pred=='empty_gripper':
            q=snapshot.get('gripper_joint_positions',[])
            if len(q)!=2:return unknown('gripper_encoder_unavailable')
            return known(max(q)>.03 or max(q)<.006,'gripper_encoder_open_or_closed_empty')
        if not temporal:return unknown('public_temporal_window_incomplete')
        if pred=='holding':
            if not o or obj not in reference:return unknown('holding_surface_or_reference_unavailable')
            offsets=[];lift=[];sample_times=[];sample_ids=set();views=set()
            for h in recent:
                v=h.get('objects',{}).get(obj,{})
                if v.get('status')!='available' or geometry_problem(v,h):return unknown('holding_window_occluded_or_invalid_source')
                # The RGB-D worker runs independently of S0. A measured point
                # belongs with the TCP pose from that image, never a later pose.
                measured=h.get('geometry_robot_state',h)
                measured_at=v.get('captured_at',measured.get('captured_at',0))
                if h['captured_at']-measured_at>THRESHOLDS['max_age_seconds']:
                    return unknown('holding_geometry_stale')
                if measured.get('control_step',h['control_step'])<snapshot.get('geometry_not_before_step',0):continue
                identity=(measured.get('control_step',h['control_step']),measured.get('captured_at'))
                if identity in sample_ids:continue
                sample_ids.add(identity);sample_times.append(measured.get('captured_at',h['captured_at']))
                views.add(v.get('camera','unspecified'))
                point=np.array(v['surface_median_xyz']);tcp=np.array(measured['tcp_xyz'])
                q=np.array(measured.get('tcp_wxyz',[1.,0.,0.,0.]))
                offsets.append(Rotation.from_quat(q[[1,2,3,0]]).inv().apply(point-tcp))
                lift.append(point[2]-reference[obj]['surface_median_xyz'][2])
            if len(offsets)<THRESHOLDS['minimum_samples'] or max(sample_times)-min(sample_times)<THRESHOLDS['window_seconds']:
                return unknown('independent_geometry_window_incomplete')
            drift=float(np.max(np.linalg.norm(np.array(offsets)-np.mean(offsets,axis=0),axis=1)))
            distance=max(np.linalg.norm(v) for v in offsets)
            metrics=dict(relative_drift_m=drift,surface_tcp_distance_m=float(distance),minimum_lift_m=float(min(lift)),
                independent_samples=len(offsets),measurement_span_seconds=max(sample_times)-min(sample_times),cameras=sorted(views),
                pairing='image_capture_robot_state' if snapshot.get('geometry_robot_state') else 'synchronous_public_packet')
            def evidence(status,reason):return dict(status=status,reason=reason,metrics=metrics)
            if snapshot.get('last_gripper_command')!='closed':return evidence('failed','gripper_not_closed')
            near=distance<THRESHOLDS['tcp_distance_m']
            if custody and custody.get('object')==obj:
                fingers=snapshot.get('gripper_joint_positions',[])
                if len(fingers)!=2:return evidence('unknown','current_gripper_encoder_missing')
                if max(fingers)<.006:return evidence('failed','closed_empty_gripper_contradicts_custody')
                if not near or drift>=THRESHOLDS['relative_drift_m']:return evidence('unknown','partial_surface_or_motion_does_not_establish_custody')
                return evidence('verified','current_relative_surface_and_gripper_support_custody')
            return evidence('verified' if min(lift)>THRESHOLDS['lift_m'] and near and drift<THRESHOLDS['relative_drift_m'] else 'failed','elevated_near_tcp_and_consistent_public_window')
        if pred in {'on','in_region'}:
            if not o or obj not in reference:return unknown('placed_object_geometry_unavailable')
            if snapshot.get('last_gripper_command')!='open' and (not custody or custody.get('object')==obj):return unknown('placement_requires_release_evidence')
            required=[obj]+([c['support']] if pred=='on' and c['support']!='table' else [])
            samples,times=placement_window(recent,required,snapshot.get('geometry_not_before_step',0))
            if len(samples)<THRESHOLDS['minimum_samples'] or max(times)-min(times)<THRESHOLDS['window_seconds']:
                return unknown('independent_placement_window_incomplete')
            observed=[]
            for h in samples:
                v=h.get('objects',{}).get(obj,{})
                if v.get('status')!='available' or h['captured_at']-v.get('captured_at',0)>1:
                    return unknown('placement_window_occluded_or_stale')
                observed.append(np.array(v['surface_median_xyz']))
            if np.max(np.linalg.norm(np.array(observed)-observed[-1],axis=1))>.012:
                return unknown('placed_object_not_stable_in_public_window')
            lo=np.array(o['surface_q02_xyz']);hi=np.array(o['surface_q98_xyz']);ref=reference[obj]
            height=ref.get('observed_height_m')
            if height is None:return unknown('object_height_unavailable')
            bottom=hi[2]-height
            if pred=='on':
                support=c['support'];sup=visible(support)
                if support=='table':return known(abs(bottom-.8)<.018,'public_object_bottom_at_calibrated_table')
                if not sup:return unknown('support_geometry_unavailable')
                if cat['objects'][support]['shape']=='box':
                    sup=support_surface(sup,reference.get(support))
                    if sup is None:return unknown('support_extent_not_established_from_current_surface')
                slo=np.array(sup['surface_q02_xyz']);shi=np.array(sup['surface_q98_xyz'])
                center=(lo[:2]+hi[:2])/2
                overlap=np.maximum(0,np.minimum(hi[:2],shi[:2])-np.maximum(lo[:2],slo[:2]))
                contained=np.all(center>=slo[:2]+.003) and np.all(center<=shi[:2]-.003) and np.prod(overlap)/max(1e-9,np.prod(hi[:2]-lo[:2]))>.4
                result=known(contained and abs(bottom-shi[2])<.018,'visible_footprint_and_support_height')
                result['metrics']=dict(object_center_xy=center.tolist(),support_lower_xy=slo[:2].tolist(),support_upper_xy=shi[:2].tolist(),
                    center_clearance_m=float(np.min(np.r_[center-slo[:2],shi[:2]-center])),
                    visible_overlap_fraction=float(np.prod(overlap)/max(1e-9,np.prod(hi[:2]-lo[:2]))),
                    bottom_support_gap_m=float(bottom-shi[2]),object_height_reference_m=height,
                    object_cameras=o.get('contributing_cameras',[o.get('camera')]),support_cameras=sup.get('contributing_cameras',[sup.get('camera')]))
                return result
            region=cat['regions'][c['region']];rlo,rhi=np.array(region['bounds_xy'])
            # Quantiles describe a visible surface, not the full occupied footprint.
            # Use public measured size only under the same planar completion guard,
            # retain observed extrema, and require clearance for measurement error.
            if np.any(lo[:2]<rlo) or np.any(hi[:2]>rhi):
                return known(False,'visible_surface_outside_calibrated_region')
            completed=support_surface(o,ref) if cat['objects'][obj]['shape']=='box' else o
            if completed is None:return unknown('region_object_extent_not_established')
            lower=np.minimum(np.array(completed['surface_q02_xyz'])[:2],np.array(o.get('surface_min_xyz',lo))[:2])
            upper=np.maximum(np.array(completed['surface_q98_xyz'])[:2],np.array(o.get('surface_max_xyz',hi))[:2])
            uncertainty=THRESHOLDS['region_boundary_uncertainty_m']
            if np.any(lower-uncertainty<rlo) or np.any(upper+uncertainty>rhi):
                return unknown('region_boundary_clearance_not_established')
            return known(abs(bottom-region['surface_z'])<.018,'public_extent_and_uncertainty_inside_calibrated_region')
        return unknown('predicate_has_no_public_verifier')
    results=[dict(condition=c,**check(c)) for c in goal['all']]
    status='failed' if any(c['status']=='failed' for c in results) else 'unknown' if any(c['status']=='unknown' for c in results) else 'verified'
    return dict(status=status,conditions=results,evidence_ref=snapshot.get('evidence_ref'),scope='observed_supported_tabletop_relations',
        geometry_evidence_source=snapshot.get('geometry_evidence_source'))
