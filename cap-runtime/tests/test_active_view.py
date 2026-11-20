import unittest
import numpy as np
from scipy.spatial.transform import Rotation
from experiments.active_view_trial import inspection_quaternion, PublicTrial


class ActiveViewTests(unittest.TestCase):
    def test_yaw_is_relative_to_original_orientation_in_base_frame(self):
        initial = Rotation.from_euler('xyz', [170, 12, 9], degrees=True)
        xyzw = initial.as_quat()
        actual = inspection_quaternion(xyzw[[3, 0, 1, 2]], -25)
        rotation = Rotation.from_quat(np.asarray(actual)[[1, 2, 3, 0]])
        np.testing.assert_allclose((rotation*initial.inv()).as_rotvec(), [0, 0, np.deg2rad(-25)], atol=1e-10)

    def observation(self, status='unknown'):
        return dict(tcp_xyz=[.5, 0., .15], tcp_wxyz=[0., 0., 1., 0.],
                    objects={'red cube': {'candidate': {'status': status}}})

    def test_inspection_is_conditional_bounded_and_stops_after_information(self):
        calls = []
        trial = PublicTrial(None, 'passive')
        trial.segment = lambda *args: calls.append(args)
        original = self.observation()
        self.assertIs(trial.inspect_if_needed(original, 'red cube'), original)
        self.assertEqual(calls, [])
        trial.mode = 'active'
        known = self.observation('estimated')
        self.assertIs(trial.inspect_if_needed(known, 'red cube'), known)
        self.assertEqual(calls, [])
        def gain(*args):
            calls.append(args)
            return self.observation('estimated')
        trial.segment = gain
        self.assertEqual(trial.inspect_if_needed(original, 'red cube')['objects']['red cube']['candidate']['status'], 'estimated')
        self.assertEqual(len(calls), 1)
        calls.clear()
        def no_gain(*args):
            calls.append(args)
            return self.observation()
        trial.segment = no_gain
        trial.inspect_if_needed(original, 'red cube')
        self.assertEqual(len(calls), 2)

    def test_failed_inspection_never_dispatches_the_next_view(self):
        calls = []
        trial = PublicTrial(None, 'active')
        def fail(*args):
            calls.append(args)
            raise RuntimeError('holding was lost')
        trial.segment = fail
        with self.assertRaises(RuntimeError):
            trial.inspect_if_needed(self.observation(), 'red cube')
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
