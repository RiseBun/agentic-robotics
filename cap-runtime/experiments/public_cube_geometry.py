"""Offline candidate, deliberately not exposed to the robot or its model.

Input is a public RGB-D packet and an earlier public size estimate. A cube
shape assumption does not make one/two visible faces a complete pose estimate.
Thresholds below are development assumptions, not statistical confidence bounds.
"""
from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation
from agentic_cap.public_sensors import GEOMETRY_PROTOCOL


PROTOCOL = dict(seed=20261118, maximum_points=768, ransac_trials=192,
                maximum_planes=3, plane_inlier_m=.003,
                minimum_plane_points=20, minimum_plane_fraction=.08,
                maximum_orthogonality_error_deg=15., minimum_face_span_fraction=.60,
                minimum_size_span_ratio=.75, minimum_explained_fraction=.90)


def surface_points(packet, name):
    """Same mask, projection and frame as the existing visible-surface rule."""
    if name not in GEOMETRY_PROTOCOL['supported_labels']:
        return np.empty((0, 3))
    rgb = packet['rgb'].astype(np.float64)
    index = 0 if name == 'red cube' else 1
    channel = rgb[:, :, index]
    other = np.max(np.delete(rgb, index, axis=2), axis=2)
    depth = packet['depth'].squeeze()
    mask = ((channel >= GEOMETRY_PROTOCOL['minimum_channel_value']) &
            (channel > GEOMETRY_PROTOCOL['dominant_channel_ratio'] * other) &
            np.isfinite(depth) & (depth > .015) & (depth < 3.))
    y, x = np.nonzero(mask)
    z = depth[mask]
    k = packet['intrinsics']
    points = np.column_stack([(x-k[0, 2])*z/k[0, 0], (y-k[1, 2])*z/k[1, 1], z])
    pose = packet['camera_pose_xyz_wxyz']
    return Rotation.from_quat(pose[3:][[1, 2, 3, 0]]).apply(points) + pose[:3]


def fit_faces(points, camera_xyz):
    result = dict(status='unknown', reason='insufficient_visible_depth',
                  point_count=len(points), scope='bounded_cube_geometry_development_candidate')
    if len(points) < GEOMETRY_PROTOCOL['minimum_pixels']:
        return result
    rng = np.random.default_rng(PROTOCOL['seed'])
    if len(points) > PROTOCOL['maximum_points']:
        points = points[np.sort(rng.choice(len(points), PROTOCOL['maximum_points'], replace=False))]
    remaining = points.copy()
    faces = []
    minimum = max(PROTOCOL['minimum_plane_points'],
                  int(np.ceil(PROTOCOL['minimum_plane_fraction']*len(points))))
    for _ in range(PROTOCOL['maximum_planes']):
        if len(remaining) < minimum:
            break
        # Sample distinct triples; degenerate triples are discarded.
        triples = np.array([rng.choice(len(remaining), 3, replace=False)
                            for _ in range(PROTOCOL['ransac_trials'])])
        a, b, c = [remaining[triples[:, i]] for i in range(3)]
        normals = np.cross(b-a, c-a)
        lengths = np.linalg.norm(normals, axis=1)
        valid = lengths > 1e-8
        if not valid.any():
            break
        normals = normals[valid]/lengths[valid, None]
        offsets = np.einsum('ij,ij->i', normals, a[valid])
        distances = np.abs(remaining @ normals.T - offsets)
        index = np.argmax((distances <= PROTOCOL['plane_inlier_m']).sum(axis=0))
        inliers = distances[:, index] <= PROTOCOL['plane_inlier_m']
        if inliers.sum() < minimum:
            break
        cloud = remaining[inliers]
        origin = cloud.mean(axis=0)
        _, _, vt = np.linalg.svd(cloud-origin, full_matrices=False)
        normal = vt[-1]
        if np.dot(normal, np.asarray(camera_xyz)-origin) < 0:
            normal = -normal
        faces.append(dict(normal=normal, cloud=cloud,
                          rms_m=float(np.sqrt(np.mean(((cloud-origin)@normal)**2)))))
        remaining = remaining[~inliers]
    result['plane_counts'] = [len(f['cloud']) for f in faces]
    result['plane_rms_m'] = [f['rms_m'] for f in faces]
    result['explained_fraction'] = 1-len(remaining)/len(points)
    if len(faces) != 3:
        return dict(result, reason='fewer_than_three_faces')
    normals = np.array([f['normal'] for f in faces])
    off_diagonal = np.abs((normals @ normals.T - np.eye(3))[np.triu_indices(3, 1)])
    result['maximum_normal_dot'] = float(off_diagonal.max())
    if off_diagonal.max() > np.sin(np.deg2rad(PROTOCOL['maximum_orthogonality_error_deg'])):
        return dict(result, reason='faces_not_orthogonal')
    if result['explained_fraction'] < PROTOCOL['minimum_explained_fraction']:
        return dict(result, reason='unexplained_surface')
    u, _, vt = np.linalg.svd(normals)
    axes = u @ vt
    offsets = np.array([np.median(f['cloud']@axis) for f, axis in zip(faces, axes)])
    spans = np.diff(np.quantile(points @ axes.T, [.01, .99], axis=0), axis=0)[0]
    result.update(status='faces_observed', reason='three_approximately_orthogonal_faces',
                  axes=axes.tolist(), offsets=offsets.tolist(), projected_spans_m=spans.tolist())
    return result


def public_size_reference(faces):
    if faces['status'] != 'faces_observed':
        return None
    spans = np.asarray(faces['projected_spans_m'])
    if spans.min() <= 0 or spans.min()/spans.max() < PROTOCOL['minimum_size_span_ratio']:
        return None
    return dict(side_m=float(np.median(spans)), scope='earlier_public_RGBD_cube_shape_assumption')


def estimate_center(faces, size_reference):
    if faces['status'] != 'faces_observed':
        return dict(status='unknown', reason=faces['reason'])
    if size_reference is None:
        return dict(status='unknown', reason='no_earlier_public_size_reference')
    side = size_reference['side_m']
    if not np.isfinite(side) or side <= 0:
        return dict(status='unknown', reason='invalid_public_size_reference')
    if min(faces['projected_spans_m']) < PROTOCOL['minimum_face_span_fraction']*side:
        return dict(status='unknown', reason='insufficient_visible_span')
    axes = np.asarray(faces['axes'])
    center = np.linalg.solve(axes, np.asarray(faces['offsets'])-side/2)
    return dict(status='estimated', center_xyz=center.tolist(),
                reason='three_face_fit_with_earlier_public_size',
                scope='cube_shape_assumption; not_calibrated_confidence_or_contact_truth')
