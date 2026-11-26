"""Bounded TCP IK and encoder/FK motion acceptance, without simulator access.

The upstream velocity-penalized IK can return a compromise far from the goal.
Refinement is computational: no joint target is submitted until its FK passes.
Tracking remains the persistent physics owner's job, through backend.move().
"""
import copy
import math
import time
import uuid

import numpy as np
from scipy.spatial.transform import Rotation

from .feedback import MotionError


MOTION_PROTOCOL = dict(version=1, max_ik_attempts=3, max_control_steps_per_phase=100,
    ik_position_tolerance_m=.001, ik_orientation_tolerance_rad=math.radians(.5),
    position_tolerance_m=.003, orientation_tolerance_rad=math.radians(1),
    joint_tolerance_rad=.02,
    scope='public encoder and robot-model FK; endpoint acceptance, not collision or path certification')


def vector(value, size):
    result = np.asarray(value, dtype=float)
    if result.shape != (size,) or not np.isfinite(result).all():
        raise ValueError('Expected a finite vector of length %d' % size)
    return result


def orientation(value):
    quat = vector(value, 4)
    norm = np.linalg.norm(quat)
    if norm < 1e-8:
        raise ValueError('Quaternion must have nonzero norm')
    return quat / norm


def pose_evidence(xyz, wxyz, target):
    xyz, quat = vector(xyz, 3), orientation(wxyz)
    desired = orientation(target['wxyz'])
    angle = (Rotation.from_quat(quat[[1, 2, 3, 0]]) *
             Rotation.from_quat(desired[[1, 2, 3, 0]]).inv()).magnitude()
    return dict(requested_xyz=list(target['xyz']), requested_wxyz=desired.tolist(),
        actual_xyz=xyz.tolist(), actual_wxyz=quat.tolist(),
        position_error_m=float(np.linalg.norm(xyz-vector(target['xyz'], 3))),
        orientation_error_rad=float(angle),
        position_tolerance_m=MOTION_PROTOCOL['position_tolerance_m'],
        orientation_tolerance_rad=MOTION_PROTOCOL['orientation_tolerance_rad'])


def reached(evidence, *, ik=False):
    prefix = 'ik_' if ik else ''
    return (evidence['position_error_m'] <= MOTION_PROTOCOL[prefix+'position_tolerance_m']
            and evidence['orientation_error_rad'] <= MOTION_PROTOCOL[prefix+'orientation_tolerance_rad'])


def solve_pose_candidate(position, quaternion_wxyz, initial_configuration, tcp_offset,
                         solver, fk, checkpoint, on_attempt=None):
    """Read-only endpoint IK check shared by motion and diagnostic preview.

    Callbacks receive copies. This function cannot submit a joint target and
    does not certify a path, collisions or future state. Rejection means only
    that this bounded warm-start solve did not meet existing FK tolerances.
    """
    pos, quat = vector(position, 3), orientation(quaternion_wxyz)
    target = dict(xyz=pos.tolist(), wxyz=quat.tolist())
    rot = Rotation.from_quat(quat[[1, 2, 3, 0]])
    payload = np.concatenate([quat, pos+rot.apply(vector(tcp_offset, 3))])
    cfg = vector(initial_configuration, 8).astype(np.float32).copy()
    attempts = []
    for attempt in range(1, MOTION_PROTOCOL['max_ik_attempts']+1):
        checkpoint()
        started = time.monotonic()
        solved = np.asarray(solver(target_pose_wxyz_xyz=payload.copy(), prev_cfg=cfg.copy()), dtype=float)
        checkpoint()
        if solved.shape != (8,) or not np.isfinite(solved).all():
            raise MotionError(dict(status='failed', reason='invalid_ik_solution', control_steps=0))
        xyz, wxyz = fk(solved[:7].copy())
        checkpoint()
        evidence = pose_evidence(xyz, wxyz, target)
        row = dict(attempt=attempt, **evidence, joint_solution=solved[:7].tolist(),
                   elapsed_seconds=time.monotonic()-started, accepted=reached(evidence, ik=True))
        attempts.append(row)
        if on_attempt is not None:
            on_attempt(copy.deepcopy(row))
        checkpoint()
        if row['accepted']:
            return dict(status='accepted', reason='bounded_ik_residual_passed', configuration=solved.tolist(),
                        ik_attempts=attempts, **evidence, control_steps=0,
                        scope='Endpoint FK residual only; no motion, path or collision certification')
        cfg = solved.copy()
    return dict(status='rejected', reason='ik_target_residual', configuration=None,
                ik_attempts=attempts, **evidence, control_steps=0,
                scope='Bounded solver rejection; not a proof that the pose is unreachable')


def install_cartesian_motion(backend, segment, api, functions):
    """Keep the registered API signature, while replacing raw goto_pose semantics."""
    e = backend.episode

    def checkpoint():
        e.checkpoint(segment)

    def emit(kind, **data):
        with e.lock:
            checkpoint()
            e.event(kind, segment_id=segment['id'], revision=segment['revision'], **data)

    def goto_pose(position, quaternion_wxyz, z_approach=0.0):
        pos, quat = vector(position, 3), orientation(quaternion_wxyz)
        approach = float(z_approach)
        if not math.isfinite(approach):
            raise ValueError('Approach distance must be finite')
        rot = Rotation.from_quat(quat[[1, 2, 3, 0]])
        phases = []
        if approach:
            phases.append(('approach', vector(pos+rot.apply([0, 0, -approach]), 3)))
        phases.append(('target', pos))
        command_id = uuid.uuid4().hex
        emit('cartesian_motion_started', command_id=command_id, requested_xyz=pos.tolist(),
             requested_wxyz=quat.tolist(), z_approach=approach, protocol=MOTION_PROTOCOL)
        completed = []
        for phase, target_xyz in phases:
            target = dict(xyz=target_xyz.tolist(), wxyz=quat.tolist())
            with e.lock:
                checkpoint()
                cfg = np.concatenate([vector(backend.encoder, 7), [0.]]).astype(np.float32)
            try:
                preview = solve_pose_candidate(target_xyz, quat, cfg, api._TCP_OFFSET,
                    api.ik_solve_fn, backend.monitor.fk, checkpoint,
                    lambda row: emit('cartesian_ik_checked', command_id=command_id, phase=phase, **row))
            except MotionError as exc:
                exc.evidence.update(command_id=command_id, phase=phase)
                raise
            attempts = preview['ik_attempts']
            if preview['status'] != 'accepted':
                failure = dict(status='failed', reason='ik_target_residual', command_id=command_id,
                    phase=phase, ik_attempts=attempts,
                    **{key:value for key,value in preview.items() if key not in
                       {'status','reason','configuration','ik_attempts','scope','control_steps'}}, control_steps=0)
                with e.lock:
                    checkpoint()
                    backend.motion_result = copy.deepcopy(failure)
                raise MotionError(failure)
            solved = np.asarray(preview['configuration'])
            # move rechecks cancellation under the same lock as target admission.
            result = backend.move(segment, solved[:7], MOTION_PROTOCOL['joint_tolerance_rad'],
                                  MOTION_PROTOCOL['max_control_steps_per_phase'], tcp_target=target)
            checkpoint()
            completed.append(dict(phase=phase, ik_attempts=attempts, **result))
        result = dict(completed[-1], command_id=command_id, phases=completed,
                      control_steps=sum(p['control_steps'] for p in completed))
        with e.lock:
            checkpoint()
            backend.motion_result = copy.deepcopy(result)
        emit('cartesian_motion_finished', command_id=command_id, result=result)
        return result

    return dict(functions, goto_pose=goto_pose)
