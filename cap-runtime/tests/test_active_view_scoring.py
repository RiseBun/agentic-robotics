import unittest
import numpy as np
from experiments.score_active_view import stable_outcome, center_reference


class ActiveViewScoringTests(unittest.TestCase):
    def test_end_window_requires_persistent_evidence(self):
        self.assertEqual(stable_outcome([{'time': 1., 'task_completed': True}], 0., 2.)['status'], 'unknown')
        rows = [{'time': t, 'task_completed': result} for t, result in [(1., True), (1.6, False)]]
        self.assertEqual(stable_outcome(rows, 0., 2.)['status'], 'failed')

    def test_actual_rotated_base_transform_and_missing_transform(self):
        rows = [dict(time=t, world_positions={'red cube': [.3, .3, 1.1]},
                     world_from_cap_robot_base_xyz_wxyz=[.5, .6, .7, 0., 0., 0., 1.]) for t in [1., 1.5]]
        result = center_reference(rows, 1.25, 'red cube')
        self.assertEqual(result['status'], 'scored')
        np.testing.assert_allclose(result['center_base_xyz'], [.2, .3, .4])
        del rows[0]['world_from_cap_robot_base_xyz_wxyz']
        self.assertEqual(center_reference(rows, 1.25, 'red cube')['status'], 'unknown')


if __name__ == '__main__':
    unittest.main()
