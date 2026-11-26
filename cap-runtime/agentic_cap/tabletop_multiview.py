"""Same-capture, calibrated RGB-D surface fusion. No simulator state is accepted."""
import numpy as np
from .tabletop_sensors import geometry,project


def track_mask(camera,ident,identities,prior,movable):
    import cv2
    rgb=np.asarray(camera['rgb'],float);chroma=rgb/np.maximum(rgb.sum(axis=2,keepdims=True),1)
    dist=np.linalg.norm(chroma-identities[ident]['chroma'],axis=2);mask=dist<.10
    for key,value in identities.items():
        if key!=ident:mask &= dist+.025<np.linalg.norm(chroma-value['chroma'],axis=2)
    depth=np.asarray(camera['depth']);yy,xx=np.indices(depth.shape);K=np.asarray(camera['intrinsics']);T=np.asarray(camera['camera_to_world'])
    local=np.stack([(xx-K[0,2])*depth/K[0,0],(yy-K[1,2])*depth/K[1,1],depth],axis=-1)
    world=local@T[:3,:3].T+T[:3,3]
    mask &= np.isfinite(depth)&(depth>.05)&(world[:,:,2]>.805)&(world[:,:,2]<1.4)&(abs(world[:,:,0])<.38)&(abs(world[:,:,1])<.38)
    if prior is not None:mask &= np.linalg.norm(world-np.asarray(prior),axis=2)<(.22 if movable else .13)
    count,labels,stats,_=cv2.connectedComponentsWithStats(mask.astype(np.uint8),8)
    if count<2:return np.zeros(depth.shape,dtype=bool)
    largest=1+int(np.argmax(stats[1:,cv2.CC_STAT_AREA]));return labels==largest


def measure_object(packet,ident,identities,prior=None,reference=None,movable=True,initial_mask=None):
    views=[]
    stamp=packet['captured_at']
    # Same control capture, valid rigid calibration, finite positive intrinsics.
    # One stale/miscalibrated configured camera makes a union untrustworthy.
    for camera in packet['cameras'].values():
        try:
            K=np.asarray(camera['intrinsics'],float);T=np.asarray(camera['camera_to_world'],float)
            valid=(K.shape==(3,3) and T.shape==(4,4) and np.isfinite(K).all() and np.isfinite(T).all()
                and K[0,0]>0 and K[1,1]>0 and abs(camera['captured_at']-stamp)<=.05
                and np.allclose(T[3],[0,0,0,1],atol=1e-6)
                and np.allclose(T[:3,:3].T@T[:3,:3],np.eye(3),atol=1e-4)
                and abs(np.linalg.det(T[:3,:3])-1)<1e-4)
        except (KeyError,ValueError,TypeError):valid=False
        if not valid:return dict(status='unknown',reason='incoherent_multiview_calibration_or_time')
    for name,camera in packet['cameras'].items():
        mask=initial_mask if name=='overview' and initial_mask is not None else track_mask(camera,ident,identities,prior,movable)
        result=geometry(camera,mask)
        if result['status']!='available':continue
        points,_=project(camera,mask);views.append(dict(name=name,camera=camera,mask=mask,geometry=result,points=points))
    if not views:return dict(status='unknown',reason='no_current_camera_geometry')
    for index,a in enumerate(views):
        for b in views[index+1:]:
            span=np.maximum(a['geometry']['surface_span_xyz'],b['geometry']['surface_span_xyz'])
            if reference is not None:span=np.maximum(span,reference['surface_span_xyz'])
            limit=np.linalg.norm(span)+.01
            distance=np.linalg.norm(np.asarray(a['geometry']['surface_median_xyz'])-b['geometry']['surface_median_xyz'])
            if distance>limit:return dict(status='unknown',reason='multiview_identity_geometry_inconsistent')
    primary=next((v for v in views if v['name']=='overview'),views[0])
    points=np.concatenate([v['points'] for v in views]);lo,hi=np.quantile(points,[.02,.98],axis=0)
    values,vectors=np.linalg.eigh(np.cov(points.T))
    return dict(status='available',camera=primary['name'],contributing_cameras=[v['name'] for v in views],
        plane_normal_xyz=vectors[:,0].tolist(),plane_residual_m=float(np.sqrt(max(0,values[0]))),
        camera_center_world=np.asarray(primary['camera']['camera_to_world'])[:3,3].tolist(),
        surface_min_xyz=points.min(axis=0).tolist(),surface_max_xyz=points.max(axis=0).tolist(),
        surface_q02_xyz=lo.tolist(),surface_q98_xyz=hi.tolist(),surface_median_xyz=np.median(points,axis=0).tolist(),
        surface_span_xyz=(hi-lo).tolist(),point_count=len(points),scope='calibrated_current_visible_surface_union_only',
        captured_at=packet['captured_at'],identity_observed_at=identities[ident]['identity_observed_at'],
        source='sam3_identity_current_multiview_rgbd_tracking',
        view_geometry={v['name']:{k:x for k,x in v['geometry'].items() if k in ['surface_q02_xyz','surface_q98_xyz','point_count']} for v in views},
        mask=primary['mask'])


def measure_single_view(packet,ident,identities,prior=None,movable=True,initial_mask=None):
    """Prefer overview, but a lost overview track must still try a measured wrist view."""
    for name in ('overview','wrist'):
        camera=packet['cameras'].get(name)
        if camera is None:continue
        # Reuse the calibrated same-capture validation of the multiview path.
        single=dict(packet,cameras={name:camera})
        measured=measure_object(single,ident,identities,prior=prior,movable=movable,
            initial_mask=initial_mask if name=='overview' else None)
        if measured['status']=='available':return measured
    return dict(status='unknown',reason='no_current_single_camera_geometry')
