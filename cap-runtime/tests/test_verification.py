import unittest
from types import SimpleNamespace
from collections import deque
import copy
import time
try:
    import numpy
except ImportError:
    numpy = None
from agentic_cap.cap_backend import CaPBackend, public_cube_center


def cube(lo, hi, median):
    return dict(status='visible', surface_q05_xyz=lo, surface_q95_xyz=hi,
                surface_median_xyz=median, surface_span_xyz=[b-a for a, b in zip(lo, hi)])


@unittest.skipUnless(numpy is not None, 'Verifier tests require existing CaP numpy dependency')
class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.e = SimpleNamespace(gripper=1.)
        self.backend = CaPBackend(self.e, '.')
        self.red = cube([0, 0, .04], [.04, .04, .08], [.02, .02, .08])
        self.green = cube([0, 0, -.01], [.04, .04, .03], [.02, .02, 0])
        self.samples = [dict(control_step=i, objects={'red cube': self.red, 'green cube': self.green}) for i in range(100, 121, 5)]
        self.backend.history = deque(self.samples)
        self.backend.tick = 129  # Processing lags; published window is still complete.
        self.snapshot = dict(gripper_joint_positions=[.04, -.04], evidence_ref='test.npz',
                             last_gripper_command='open', captured_at=time.time(), control_step=120)
        self.goal = dict(on=['red cube', 'green cube'], empty_gripper=True)

    def test_published_window_and_visible_box_center(self):
        self.assertEqual(self.backend.verify(self.goal, self.snapshot)['status'], 'verified')

    def test_occluded_support_is_unknown(self):
        for sample in self.samples:
            sample['objects'] = {'red cube': self.red, 'green cube': {'status': 'unknown'}}
        self.assertEqual(self.backend.verify(self.goal, self.snapshot)['status'], 'unknown')

    def test_adjacent_same_height_is_never_a_verified_stack(self):
        for sample in self.samples:
            sample['objects'] = {'red cube': self.green, 'green cube': self.green}
        self.assertNotEqual(self.backend.verify(self.goal, self.snapshot)['status'], 'verified')

    def test_insufficient_window_is_unknown(self):
        self.backend.history = deque(self.samples[-2:])
        self.assertEqual(self.backend.verify(self.goal, self.snapshot)['status'], 'unknown')

    def test_readiness_uses_post_action_published_window_not_physics_ticks(self):
        snapshot = dict(self.snapshot, captured_at=time.time(), control_step=120)
        self.assertEqual(self.backend.verification_readiness([self.goal], snapshot)['status'], 'ready')
        self.backend.last_action_tick = 125
        self.backend.tick = 1000  # Physics continuing cannot replace missing sensor frames.
        self.assertEqual(self.backend.verification_readiness([self.goal], snapshot)['status'], 'pending')
        self.backend.history = deque([dict(s, control_step=s['control_step']+30) for s in self.samples])
        snapshot.update(control_step=150, captured_at=time.time())
        self.assertEqual(self.backend.verification_readiness([self.goal], snapshot)['status'], 'ready')
        snapshot['captured_at'] -= 2
        self.assertEqual(self.backend.verification_readiness([self.goal], snapshot)['status'], 'pending')

    def test_occlusion_is_not_a_reason_to_extend_a_complete_window(self):
        for sample in self.samples:
            sample['objects'] = {'red cube': self.red, 'green cube': {'status': 'unknown'}}
        snapshot = dict(self.snapshot, captured_at=time.time(), control_step=120)
        self.assertEqual(self.backend.verification_readiness([self.goal], snapshot)['status'], 'ready')
        self.assertEqual(self.backend.verify(self.goal, snapshot)['status'], 'unknown')

    def test_pre_action_frames_cannot_verify_a_post_action_goal(self):
        self.backend.last_action_tick = 129
        self.assertEqual(self.backend.verify(self.goal, self.snapshot)['status'], 'unknown')

    def test_missing_gripper_measurement_cannot_verify_empty(self):
        self.assertEqual(self.backend.verify(self.goal, {})['status'], 'unknown')

    def test_unpublished_history_cannot_supply_a_complete_observation_window(self):
        # Sensor processing is ahead of the frame actually cited by Episode.
        snapshot = dict(self.snapshot, control_step=100)
        self.assertEqual(self.backend.verify(self.goal, snapshot)['status'], 'unknown')
        self.assertEqual(self.backend.verification_readiness([self.goal], snapshot)['status'], 'pending')

    def test_geometry_window_never_extends_beyond_the_cited_frame(self):
        self.backend.history.extend([dict(self.samples[-1], control_step=125), dict(self.samples[-1], control_step=130)])
        result = self.backend.verify(self.goal, self.snapshot)
        self.assertEqual(result['status'], 'verified')
        geometry = result['evidence'][-1]['geometry']
        self.assertEqual(max(item['control_step'] for item in geometry), self.snapshot['control_step'])

    def test_single_side_face_is_not_the_support_center(self):
        # Public earlier silhouette gives a 4 cm cube. Only its +X face remains
        # visible after placement; treating that face as the centre biases 2 cm.
        support = cube([.019, 0, -.01], [.021, .04, .03], [.02, .02, .01])
        support['visible_plane'] = dict(normal=[1., 0., 0.], offset=.02, rms_m=.0002)
        prior = cube([-.02, 0, -.01], [.02, .04, .03], [0, .02, .01])
        center, method = public_cube_center(support, prior)
        self.assertAlmostEqual(center[0], 0.)
        self.assertIn('prior_rgbd', method)
        # No prior size, a slanted face, or a poor plane cannot justify this.
        self.assertEqual(public_cube_center(support)[1], 'visible_box')
        support['visible_plane']['rms_m'] = .008
        self.assertEqual(public_cube_center(support, prior)[1], 'visible_box')

    def test_verified_hold_exposes_measured_offset_only_for_fixed_orientation(self):
        from agentic_cap.holding import holding_evidence
        low = cube([0,0,0],[.04,.04,.04],[.02,.02,.02])
        high = cube([0,0,.10],[.04,.04,.14],[.02,.02,.12])
        reference = dict(objects={'red cube':low})
        samples = [dict(control_step=i,tcp_xyz=[0,.01,.13],tcp_wxyz=[0,0,1,0],
            last_gripper_command='closed',objects={'red cube':high}) for i in range(0,21,5)]
        result = holding_evidence(reference,samples)
        self.assertEqual(result['state'],'satisfied')
        numpy.testing.assert_allclose(result['estimated_center_tcp_offset_xyz'],[.02,.01,-.01])
        self.assertEqual(result['reference_tcp_wxyz'],[0,0,1,0])
        self.assertIn('translation only',result['placement_scope'])
        result = holding_evidence(reference,samples[:2])
        self.assertEqual(result['state'],'unknown')
        self.assertNotIn('estimated_center_tcp_offset_xyz',result)


if __name__ == '__main__':
    unittest.main()
