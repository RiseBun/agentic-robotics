"""Public robot feedback and an explicit red/green cube RGB-D baseline.

No object poses, instance segmentation, contacts, rewards, or completion flags
are read. The geometry describes visible surfaces, not true object centers.
Thresholds are development choices, not calibrated probability estimates.
"""
from __future__ import annotations

import copy
import numpy as np
from scipy.spatial.transform import Rotation


GEOMETRY_PROTOCOL = {
    'supported_labels': ['red cube', 'green cube'],
    'minimum_pixels': 40,
    'dominant_channel_ratio': 1.35,
    'minimum_channel_value': 40,
    'depth_range_m': [0.015, 3.0],
    'position_tolerance_m': 0.010,
    'orientation_tolerance_deg': 10.0,
}

COORDINATE_CONVENTION = dict(frame='cap_robot_base',
    definition='CaP-X Panda fixed_mount0_base; camera and TCP expressed in this frame',
    length_unit='m', angle_unit='rad', quaternion_order='WXYZ')


def mask_public_support(rgb, depth, object_name='green cube'):
    """Mask a public RGB-D support for a controlled evidence experiment.

    This fault is applied to the rendered public packet and uses only the same
    color segmentation available to the public sensor. It never reads object
    poses, contacts, or simulator truth.
    """
    image = np.array(rgb, copy=True)
    distance = np.array(depth, copy=True)
    if image.ndim != 3 or image.shape[-1] < 3:
        raise ValueError('RGB image must be HxWx3')
    channel = {'red cube': 0, 'green cube': 1}.get(object_name)
    if channel is None:
        raise ValueError('Unsupported public object')
    other = np.max(np.delete(image[:, :, :3], channel, axis=2), axis=2)
    mask = (image[:, :, channel] >= 40) & (image[:, :, channel] > 1.35 * other)
    ys, xs = np.nonzero(mask)
    if not len(xs):
        return image, distance, False
    # Mask only support-colored pixels: the held cube must remain observable
    # even when its silhouette overlaps the support's image bounding box.
    image[mask] = 0
    distance[mask] = 0
    return image, distance, True


def public_packet(observation):
    """Allowlist sensor values; explicitly exclude privileged simulator fields."""
    camera = observation['robot0_robotview']
    return {
        'coordinate_frame': COORDINATE_CONVENTION['frame'],
        'length_unit': COORDINATE_CONVENTION['length_unit'],
        'angle_unit': COORDINATE_CONVENTION['angle_unit'],
        'quaternion_order': COORDINATE_CONVENTION['quaternion_order'],
        'joint_positions': np.array(observation['robot_joint_pos'][:7], copy=True),
        'gripper_joint_positions': np.array(observation['robot0_gripper_qpos'], copy=True),
        'rgb': np.array(camera['images']['rgb'], copy=True),
        'depth': np.array(camera['images']['depth'], copy=True),
        'intrinsics': np.array(camera['intrinsics'], copy=True),
        'camera_pose_xyz_wxyz': np.array(camera['pose'], copy=True),
    }


def projected_surface(packet, object_name):
    """Current color/depth samples in the calibrated robot base frame."""
    if object_name not in GEOMETRY_PROTOCOL['supported_labels']:
        return np.empty((0, 3)), 0, 0
    rgb = packet['rgb'].astype(np.float64)
    index = 0 if object_name == 'red cube' else 1
    channel = rgb[:, :, index]
    other = np.max(np.delete(rgb, index, axis=2), axis=2)
    mask = ((channel >= GEOMETRY_PROTOCOL['minimum_channel_value']) &
            (channel > GEOMETRY_PROTOCOL['dominant_channel_ratio'] * other))
    depth = packet['depth'].squeeze()
    valid = mask & np.isfinite(depth) & (depth > .015) & (depth < 3.0)
    count = int(valid.sum())
    colors = int(mask.sum())
    if count < GEOMETRY_PROTOCOL['minimum_pixels']:
        return np.empty((0, 3)), colors, count
    y, x = np.nonzero(valid)
    z = depth[valid]
    k = packet['intrinsics']
    points = np.column_stack([(x-k[0, 2])*z/k[0, 0], (y-k[1, 2])*z/k[1, 1], z])
    pose = packet['camera_pose_xyz_wxyz']
    points = Rotation.from_quat(pose[3:][[1, 2, 3, 0]]).apply(points) + pose[:3]
    return points, colors, count


def visible_surface(packet, object_name):
    if object_name not in GEOMETRY_PROTOCOL['supported_labels']:
        return {'status': 'unknown', 'reason': 'unsupported_label'}
    points, colors, count = projected_surface(packet, object_name)
    result = {'status': 'unknown', 'object_name': object_name,
              'color_pixels': colors, 'valid_depth_pixels': count,
              'estimator': 'dominant_rgb_visible_surface; task_specific_baseline'}
    if count < GEOMETRY_PROTOCOL['minimum_pixels']:
        return dict(result, reason='insufficient_visible_depth')
    pose = packet['camera_pose_xyz_wxyz']
    lo, hi = np.quantile(points, [.05, .95], axis=0)
    center = np.median(points, axis=0)
    result.update(status='visible', reason='surface_observed_not_center_verified',
                  surface_median_xyz=center.tolist(),
                  surface_q05_xyz=lo.tolist(), surface_q95_xyz=hi.tolist(),
                  surface_span_xyz=(hi-lo).tolist())
    # A single visible face is not the object's centre. Publish its measured
    # plane so the bounded cube verifier can use an earlier RGB-D size estimate.
    # No simulator dimensions or object poses are consulted.
    origin = points.mean(axis=0)
    values, vectors = np.linalg.eigh(np.cov(points.T))
    normal = vectors[:, 0]
    if np.dot(normal, pose[:3]-origin) < 0:
        normal = -normal
    result['visible_plane'] = dict(normal=normal.tolist(),
        offset=float(np.dot(normal, origin)), rms_m=float(np.sqrt(max(0., values[0]))))
    return result


class PublicExecutionMonitor:
    def __init__(self, robot, reader):
        self.robot = robot
        self.reader = reader
        self.last_motion = None
        self.segments = []
        self.last_gripper_command = 'unknown'
        self.step_reader = lambda: None
        self.last_home_memory_sync = None

    def fk(self, joints):
        import jax.numpy as jnp
        import jaxlie
        cfg = np.concatenate([np.asarray(joints)[:7], [0.]])
        hand = jaxlie.SE3(self.robot.forward_kinematics(jnp.array(cfg))[
            self.robot.links.names.index('panda_hand')])
        quat = np.array(hand.rotation().wxyz)
        xyz = np.array(hand.translation()) - Rotation.from_quat(quat[[1,2,3,0]]).apply([0,0,-.107])
        return xyz, quat

    def state(self, packet=None):
        packet = self.reader() if packet is None else packet
        xyz, quat = self.fk(packet['joint_positions'])
        return {'coordinate_convention': copy.deepcopy(COORDINATE_CONVENTION),
                'tcp_xyz': xyz.tolist(), 'tcp_wxyz': quat.tolist(),
                'control_step': self.step_reader(),
                'joint_positions': packet['joint_positions'].tolist(),
                'gripper_joint_positions': packet['gripper_joint_positions'].tolist(),
                'last_gripper_command': self.last_gripper_command,
                'last_home_memory_sync': copy.deepcopy(self.last_home_memory_sync),
                'objects': {name: visible_surface(packet, name)
                            for name in GEOMETRY_PROTOCOL['supported_labels']},
                'last_motion': copy.deepcopy(self.last_motion)}
