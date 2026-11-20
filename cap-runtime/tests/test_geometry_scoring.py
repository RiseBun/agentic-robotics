"""Test the coordinate/time boundary of the offline geometry evaluation."""
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from experiments.score_public_geometry import score


class GeometryScoringTests(unittest.TestCase):
    def score_case(self, truth):
        with tempfile.TemporaryDirectory() as directory:
            trial = Path(directory)/'trial'
            trial.mkdir()
            with zipfile.ZipFile(trial/'runtime.zip', 'w') as archive:
                archive.writestr('episode/evaluation-private.jsonl',
                                 ''.join(json.dumps(row)+'\n' for row in truth))
            row = dict(trial='trial', object_name='red cube', requested_by_model=True,
                       evidence_ref='test.npz', captured_at=10.25, gripper_command=0.,
                       candidate=dict(status='estimated', center_xyz=[.2, .3, .4]),
                       public_size_reference=None, cpu_elapsed_ms=1.,
                       old_visible_box_center_xyz=[.2, .3, .4])
            return score([row], Path(directory), [.5, .6, .7], [0., 0., 0., 1.])[0]

    def stable_truth(self):
        # 180-degree rotation about Z: base [.2,.3,.4] -> world [.3,.3,1.1].
        return [dict(time=t, world_positions={'red cube': [.3, .3, 1.1]},
                     bilateral_contact={'red cube': True}) for t in [10., 10.5]]

    def test_rotated_translated_frame_is_applied_before_errors(self):
        row = self.score_case(self.stable_truth())
        self.assertEqual(row['scoring_status'], 'scored')
        self.assertLess(row['old_xyz_error_m'], 1e-12)
        self.assertLess(row['candidate_xyz_error_m'], 1e-12)

    def test_moving_or_missing_truth_is_unknown(self):
        truth = self.stable_truth()
        truth[1]['world_positions']['red cube'][0] += .03
        row = self.score_case(truth)
        self.assertEqual(row['scoring_status'], 'unknown')
        self.assertNotIn('candidate_xy_error_m', row)
        row = self.score_case(self.stable_truth()[:1])
        self.assertEqual(row['reason'], 'no_bracketing_truth')


if __name__ == '__main__':
    unittest.main()
