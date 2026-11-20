import copy
import threading
import time
import unittest
from types import SimpleNamespace

import numpy as np
from scipy.spatial.transform import Rotation

from agentic_cap.cap_backend import CaPBackend
from agentic_cap.cartesian_motion import install_cartesian_motion, pose_evidence, reached
from agentic_cap.core import Halt
from agentic_cap.feedback import MotionError
from agentic_cap.interpreter import Interpreter


Q = [0., 0., 1., 0.]
P = [.4, .1, .12]


def fk(joints):
    q = Rotation.from_rotvec(np.array(joints)[3:6]).as_quat()
    return np.array(joints)[:3], q[[3, 0, 1, 2]]


def solve(target_pose_wxyz_xyz, prev_cfg):
    q = np.array(target_pose_wxyz_xyz[:4])
    rot = Rotation.from_quat(q[[1, 2, 3, 0]])
    tcp = np.array(target_pose_wxyz_xyz[4:]) - rot.apply([0, 0, -.107])
    return np.concatenate([tcp, rot.as_rotvec(), [0., 0.]])


class Harness:
    def __init__(self, solver=solve, offset=None):
        lock = threading.RLock()
        self.segment = dict(id='segment', revision=1, cancel_requested=False)
        self.e = SimpleNamespace(lock=lock, cv=threading.Condition(lock), revision=1,
                                 target=None, motion=False, status='ready')
        self.events, self.moves, self.solves = [], [], []
        def checkpoint(segment):
            if segment['cancel_requested'] or segment['revision'] != self.e.revision:
                raise Halt('Cancelled', 'CANCELLED')
            if self.e.status != 'ready':
                raise Halt('Budget exhausted', 'BUDGET_EXHAUSTED')
        self.e.checkpoint = checkpoint
        self.e.event = lambda kind, **row: self.events.append(dict(kind=kind, **row))
        self.backend = object.__new__(CaPBackend)
        self.backend.episode = self.e
        self.backend.encoder = np.zeros(7)
        self.backend.tick = 0
        self.backend.motion_result = None
        self.backend.monitor = SimpleNamespace(fk=fk)
        def ik(**kwargs):
            self.solves.append(copy.deepcopy(kwargs))
            return solver(**kwargs)
        api = SimpleNamespace(_TCP_OFFSET=[0, 0, -.107], ik_solve_fn=ik)
        def move(*args, **kwargs):
            self.moves.append(copy.deepcopy(kwargs['tcp_target']))
            return CaPBackend.move(self.backend, *args, **kwargs)
        self.backend.move = move
        self.fn = install_cartesian_motion(self.backend, self.segment, api, {})['goto_pose']
        self.done = threading.Event()
        def physics():
            motion_ticks = 0
            while not self.done.wait(.002):
                with self.e.cv:
                    self.backend.tick += 1
                    if self.e.target is not None:
                        motion_ticks += 1
                        delta = offset(motion_ticks) if offset else np.zeros(7)
                        self.backend.encoder = np.array(self.e.target)+delta
                    self.e.cv.notify_all()
        self.thread = threading.Thread(target=physics, daemon=True)
        self.thread.start()

    def close(self):
        self.done.set()
        self.thread.join(2)


class CartesianMotionTests(unittest.TestCase):
    def harness(self, *args, **kwargs):
        h = Harness(*args, **kwargs)
        self.addCleanup(h.close)
        return h

    def test_refines_bad_ik_before_any_joint_dispatch(self):
        count = 0
        def approximate(**kwargs):
            nonlocal count
            count += 1
            result = solve(**kwargs)
            if count == 1:
                result[0] += .05
            return result
        h = self.harness(approximate)
        result = h.fn(P, Q)
        self.assertEqual(len(h.solves), 2)
        self.assertEqual(len(h.moves), 1)
        self.assertAlmostEqual(h.solves[1]['prev_cfg'][0], P[0]+.05)
        self.assertEqual(result['status'], 'reached')
        self.assertLessEqual(result['position_error_m'], .003)
        self.assertEqual(result['scope'], 'cartesian_target')

    def test_persistent_ik_error_is_bounded_and_never_moves_or_releases(self):
        def inaccurate(**kwargs):
            result = solve(**kwargs)
            result[0] += .05
            return result
        h = self.harness(inaccurate)
        released = []
        with self.assertRaises(MotionError) as context:
            Interpreter({'goto_pose': h.fn, 'open_gripper': lambda: released.append(True)},
                        lambda: h.e.checkpoint(h.segment)).run(
                            'goto_pose([.4,.1,.12],[0,0,1,0])\nopen_gripper()')
        self.assertEqual(context.exception.evidence['reason'], 'ik_target_residual')
        self.assertEqual(len(h.solves), 3)
        self.assertEqual(h.moves, [])
        self.assertIsNone(h.e.target)
        self.assertEqual(released, [])

    def test_joint_arrival_cannot_hide_tcp_tracking_error(self):
        h = self.harness(offset=lambda _n: np.array([.01, 0, 0, 0, 0, 0, 0]))
        with self.assertRaises(MotionError) as context:
            h.fn(P, Q)
        result = context.exception.evidence
        self.assertEqual(result['reason'], 'cartesian_tracking_timeout')
        self.assertLess(result['joint_error_rad'], .02)
        self.assertGreater(result['position_error_m'], .003)

    def test_waits_for_tcp_settle_after_joint_tolerance_is_met(self):
        h = self.harness(offset=lambda n: np.array([.01 if n < 6 else 0, 0, 0, 0, 0, 0, 0]))
        result = h.fn(P, Q)
        self.assertEqual(result['status'], 'reached')
        self.assertGreaterEqual(result['control_steps'], 6)

    def test_orientation_error_also_blocks_success(self):
        h = self.harness(offset=lambda _n: np.array([0, 0, 0, 0, .019, 0, 0]))
        with self.assertRaises(MotionError) as context:
            h.fn(P, Q)
        self.assertEqual(context.exception.evidence['reason'], 'cartesian_tracking_timeout')
        self.assertGreater(context.exception.evidence['orientation_error_rad'], np.radians(1))

    def test_quaternion_sign_is_the_same_orientation(self):
        self.assertTrue(reached(pose_evidence(P, [0, 0, -1, 0], dict(xyz=P, wxyz=Q))))

    def test_approach_and_final_target_are_independently_accepted(self):
        h = self.harness()
        result = h.fn(P, Q, z_approach=.1)
        self.assertEqual(len(h.moves), 2)
        self.assertAlmostEqual(h.moves[0]['xyz'][2], P[2]+.1)
        self.assertEqual(h.moves[1]['xyz'], P)
        self.assertEqual([p['phase'] for p in result['phases']], ['approach', 'target'])
        self.assertTrue(all(p['status']=='reached' for p in result['phases']))

    def late_solver(self, mutation):
        entered, release = threading.Event(), threading.Event()
        def slow(**kwargs):
            entered.set()
            if not release.wait(2):
                raise TimeoutError('Test solver not released')
            return solve(**kwargs)
        h = self.harness(slow)
        errors = []
        def work():
            try:
                h.fn(P, Q)
            except Exception as exc:
                errors.append(exc)
        worker = threading.Thread(target=work)
        worker.start()
        self.assertTrue(entered.wait(2))
        mutation(h)
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], Halt)
        self.assertIsNone(h.e.target)
        self.assertEqual(h.moves, [])
        self.assertFalse(any(e['kind']=='cartesian_ik_checked' for e in h.events))
        return errors[0]

    def test_cancel_fences_late_ik_reply(self):
        self.late_solver(lambda h: h.segment.update(cancel_requested=True))

    def test_goal_change_fences_late_ik_reply(self):
        self.late_solver(lambda h: setattr(h.e, 'revision', 2))

    def test_budget_expiry_fences_late_ik_reply(self):
        error = self.late_solver(lambda h: setattr(h.e, 'status', 'budget_exhausted'))
        self.assertEqual(error.code, 'BUDGET_EXHAUSTED')

    def test_bad_inputs_and_bad_solver_output_never_dispatch(self):
        h = self.harness()
        for p, q in [([0, 0], Q), (P, [0, 0, 0, 0]), (P, [float('nan'), 0, 0, 1])]:
            with self.subTest(p=p, q=q), self.assertRaises(ValueError):
                h.fn(p, q)
        self.assertEqual(h.solves, [])
        broken = self.harness(lambda **_kwargs: np.full(8, np.nan))
        with self.assertRaises(MotionError):
            broken.fn(P, Q)
        self.assertEqual(broken.moves, [])

    def test_joint_only_home_is_scoped_to_joint_target(self):
        h = self.harness()
        result = CaPBackend.move(h.backend, h.segment, np.zeros(7), .02, 100)
        self.assertEqual(result['scope'], 'joint_target')
        self.assertNotIn('position_error_m', result)


if __name__ == '__main__':
    unittest.main()
