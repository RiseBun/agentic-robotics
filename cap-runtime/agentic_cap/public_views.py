"""Calibrated RGB-D only, from a fixed allowlist of existing simulator cameras.

Only the physics owner can render. Camera/robot calibration is public sensor
metadata; this reader never asks for object poses, segmentation, contacts or reward.
"""
import copy
import threading
import math
import time
import numpy as np
from .public_sensors import public_packet, visible_surface, projected_surface, COORDINATE_CONVENTION

PRIMARY_CAMERA = 'robot0_robotview'
ALLOWED_CAMERAS = (PRIMARY_CAMERA, 'agentview', 'frontview', 'robot0_eye_in_hand')
MAX_VIEW_SKEW_SECONDS = .15


def read_camera_packet(backend, name, apply_fault=False):
    if name not in ALLOWED_CAMERAS:
        raise ValueError('Unregistered public camera')
    if threading.get_ident() != backend.episode.thread.ident:
        raise RuntimeError('Only the physics owner may read a camera')
    low, rs = backend.low, backend.low.robosuite_env
    rgb, depth = rs.sim.render(camera_name=name, width=low._render_width,
                               height=low._render_height, depth=True)
    masked = False
    if apply_fault:
        rgb, depth, masked = backend.apply_camera_fault(rgb, depth)
    raw = {name+'_image': rgb,
           name+'_depth': depth[..., None] if depth.ndim == 2 else depth}
    original = low.render_camera_names
    try:
        low.render_camera_names = [name]
        # Same upstream RGB-D decoding and calibrated base-frame transform as
        # the existing primary sensor. No privileged observation dictionary.
        low._process_camera_observations(raw)
    finally:
        low.render_camera_names = original
    camera = raw[name]
    packet = public_packet(dict(robot_joint_pos=np.asarray(backend.encoder),
        robot0_gripper_qpos=np.array(rs.sim.data.qpos[rs.robots[0]._ref_gripper_joint_pos_indexes['right']], copy=True),
        robot0_robotview=dict(images={key: camera['images'][key] for key in ('rgb', 'depth')},
                             intrinsics=camera['intrinsics'], pose=camera['pose'])))
    packet['camera_name'] = name
    if apply_fault:
        packet['support_camera_fault_masked'] = masked
    return packet


def camera_snapshot(packet):
    state = dict(camera_name=str(packet['camera_name']),
                coordinate_convention=copy.deepcopy(COORDINATE_CONVENTION),
                captured_at=packet['captured_at'], control_step=packet['control_step'],
                evidence_ref=packet['evidence_ref'], image_ref=packet['image_ref'],
                objects={name: visible_surface(packet, name) for name in ('red cube', 'green cube')})
    if 'episode_id' in packet:
        state['episode_id'] = packet['episode_id']
    return state


def choose_support_view(snapshot, support, reference, check):
    """Choose a coherent, fresh view; never merge pixels or stale geometry.

    The checker and its geometry thresholds are identical for every camera.
    An available view is visibility evidence, not a completed robot task.
    """
    primary = check(snapshot, support, reference)
    result = dict(primary, camera_name=PRIMARY_CAMERA)
    if primary['status'] == 'available':
        return result
    stamp, step = snapshot.get('captured_at'), snapshot.get('control_step')
    number = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    if not number(stamp) or not 0 <= time.time()-stamp <= 1 or not isinstance(step, int) or isinstance(step, bool):
        return result
    if snapshot.get('coordinate_convention') != COORDINATE_CONVENTION or not snapshot.get('episode_id'):
        return dict(result, secondary_view_rejected='missing_primary_frame_or_episode')
    checks, coherent_names = [], []
    for name, view in sorted(snapshot.get('camera_views', {}).items()):
        if name not in ALLOWED_CAMERAS or name == PRIMARY_CAMERA:
            checks.append(dict(camera_name=name, status='unknown', reason='unregistered_view')); continue
        other_stamp = view.get('captured_at')
        stem = 'view-%s-%06d' % (name, step)
        coherent = (view.get('camera_name') == name and view.get('episode_id') == snapshot['episode_id']
            and view.get('coordinate_convention') == COORDINATE_CONVENTION and view.get('control_step') == step
            and number(other_stamp) and abs(other_stamp-stamp) <= MAX_VIEW_SKEW_SECONDS
            and 0 <= time.time()-other_stamp <= 1
            and view.get('evidence_ref') == stem+'.npz' and view.get('image_ref') == stem+'.png')
        if not coherent:
            checks.append(dict(camera_name=name, status='unknown', reason='incoherent_view_bundle')); continue
        coherent_names.append(name)
        prior = (reference or {}).get('camera_views', {}).get(name, reference)
        evidence = check(view, support, prior)
        checks.append(dict(camera_name=name, status=evidence['status'], reason=evidence['reason'],
                           evidence_ref=evidence.get('evidence_ref')))
        if evidence['status'] == 'available':
            return dict(evidence, camera_name=name, image_ref=view['image_ref'],
                        primary_evidence=primary, view_checks=checks,
                        support_geometry=copy.deepcopy(view['objects'][support]))
    fusion = snapshot.get('support_view_fusions', {}).get(support, {})
    sources = fusion.get('camera_names', [])
    if (fusion.get('status') == 'candidate' and len(sources) == 2 and sources[0] == PRIMARY_CAMERA
            and sources[1] in coherent_names and fusion.get('episode_id') == snapshot['episode_id']
            and fusion.get('control_step') == step and fusion.get('coordinate_convention') == COORDINATE_CONVENTION
            and fusion.get('captured_at') == min(stamp, snapshot['camera_views'][sources[1]]['captured_at'])
            and fusion.get('evidence_refs') == [snapshot['evidence_ref'], snapshot['camera_views'][sources[1]]['evidence_ref']]
            and fusion.get('image_refs') == [snapshot['image_ref'], snapshot['camera_views'][sources[1]]['image_ref']]
            and all(v.get('objects', {}).get(support, {}).get('status') == 'visible'
                    and v['objects'][support].get('valid_depth_pixels', 0) >= 40
                    for v in (snapshot, snapshot['camera_views'][sources[1]]))):
        evidence = check(fusion, support, reference)
        if evidence['status'] == 'available':
            return dict(evidence, camera_name='public_rgbd_union', camera_names=sources,
                evidence_refs=fusion['evidence_refs'], image_refs=fusion['image_refs'], primary_evidence=primary,
                view_checks=checks, support_geometry=copy.deepcopy(fusion['objects'][support]),
                scope='current calibrated RGB-D surface union; support visibility only, not goal completion')
    return dict(result, view_checks=checks, fusion_reason=fusion.get('reason'))


def fuse_public_surface(primary, secondary, object_name, reference):
    """Join measured points, not view bounding boxes or remembered positions."""
    failure = dict(status='unknown', reason='incoherent_or_missing_public_views')
    if (primary.get('camera_name') != PRIMARY_CAMERA or secondary.get('camera_name') not in ALLOWED_CAMERAS[1:]
            or primary.get('episode_id') != secondary.get('episode_id') or not primary.get('episode_id')
            or primary.get('control_step') != secondary.get('control_step')):
        return failure
    stamps = [p.get('captured_at') for p in (primary, secondary)]
    if (any(not isinstance(t, (int,float)) or isinstance(t,bool) or not math.isfinite(t)
            or not 0 <= time.time()-t <= 1 for t in stamps)
            or abs(stamps[0]-stamps[1]) > MAX_VIEW_SKEW_SECONDS):
        return dict(failure, reason='stale_or_unsynchronized_views')
    try:
        points = []
        for packet in (primary, secondary):
            k, pose = np.asarray(packet['intrinsics']), np.asarray(packet['camera_pose_xyz_wxyz'])
            if (packet.get('coordinate_frame') != 'cap_robot_base' or packet.get('length_unit') != 'm'
                    or packet.get('quaternion_order') != 'WXYZ' or k.shape != (3,3) or pose.shape != (7,)
                    or not np.isfinite(k).all() or not np.isfinite(pose).all()
                    or k[0,0] <= 0 or k[1,1] <= 0 or np.linalg.norm(pose[3:]) < 1e-8):
                return dict(failure, reason='invalid_public_calibration')
            cloud, _, count = projected_surface(packet, object_name)
            if count < 40 or len(cloud) < 40:
                return dict(failure, reason='each_view_requires_visible_depth')
            points.append(cloud)
        baseline = (reference or {}).get('objects', {}).get(object_name, {}).get('surface_span_xyz', [])
        if len(baseline) != 3 or not all(isinstance(v,(int,float)) and math.isfinite(v) and v > 0 for v in baseline):
            return dict(failure, reason='public_size_reference_missing')
        scale = max(baseline)
        if not .025 <= scale <= .07:
            return dict(failure, reason='outside_bounded_cube_reference')
        cloud = np.concatenate(points)
        lo, hi = np.quantile(cloud, [.05,.95], axis=0)
        spans = hi-lo
        # Additional rejection for inconsistent calibration/label association.
        # This never lowers the existing single-view visibility threshold.
        if max(spans) > 1.5*scale:
            return dict(failure, reason='view_union_exceeds_public_cube_reference')
        obj = dict(status='visible', object_name=object_name, valid_depth_pixels=len(cloud),
            surface_median_xyz=np.median(cloud,axis=0).tolist(), surface_q05_xyz=lo.tolist(),
            surface_q95_xyz=hi.tolist(), surface_span_xyz=spans.tolist(),
            estimator='measured_current_calibrated_rgbd_union; surface_not_center_verified')
        return dict(status='candidate', reason='measured_points_joined_pending_existing_visibility_check',
            episode_id=primary['episode_id'], coordinate_convention=copy.deepcopy(COORDINATE_CONVENTION),
            control_step=primary['control_step'], captured_at=min(stamps), evidence_ref=primary['evidence_ref'],
            evidence_refs=[primary['evidence_ref'],secondary['evidence_ref']],
            image_refs=[primary['image_ref'],secondary['image_ref']],
            camera_names=[primary['camera_name'],secondary['camera_name']], objects={object_name:obj})
    except (KeyError,TypeError,ValueError,IndexError):
        return dict(failure, reason='invalid_public_view_packet')


def save_camera_packet(directory, stem, packet):
    from PIL import Image
    if not stem or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in stem):
        raise ValueError('Invalid camera artifact name')
    packet = dict(packet, evidence_ref=stem+'.npz', image_ref=stem+'.png')
    image_temp = directory / (stem+'.png.tmp')
    Image.fromarray(packet['rgb']).save(image_temp, format='PNG')
    image_temp.replace(directory / packet['image_ref'])
    depth_temp = directory / (stem+'.npz.tmp')
    recorded = dict(packet, depth=np.round(packet['depth'], 3).astype(np.float32), depth_quantization_m=.001)
    with depth_temp.open('wb') as stream:
        np.savez_compressed(stream, **recorded)
    depth_temp.replace(directory / packet['evidence_ref'])
    return camera_snapshot(packet)
