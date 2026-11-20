import unittest
from types import SimpleNamespace
import copy
from experiments.placement_view_trial import support_evidence, observation_translations, PlacementViewTrial


def measurement(spans=(.035, .036, .037), status='visible'):
    return dict(tcp_xyz=[.5, .1, .15], tcp_wxyz=[0., 0., 1., 0.], evidence_ref='frame-1.npz',
                objects={'red cube': {'candidate': {'status': 'estimated'}},
                         'green cube': {'candidate': {'status': 'unknown'},
                                        'visible': {'status': status, 'surface_span_xyz': list(spans),
                                                    'valid_depth_pixels': 100}}})


class PlacementViewTests(unittest.TestCase):
    def test_missing_and_thin_support_are_not_ready_despite_known_held_center(self):
        self.assertEqual(support_evidence(measurement(status='unknown'), 'green cube')['reason'], 'support_not_visible')
        self.assertEqual(support_evidence(measurement((.0005, .026, .028)), 'green cube')['status'], 'unknown')
        self.assertEqual(support_evidence(measurement(), 'green cube')['status'], 'available')

    def test_reference_scale_prevents_truncated_small_patch_passing(self):
        obs = measurement((.01, .011, .012))
        obs['objects']['green cube']['size_reference'] = {'side_m': .04}
        self.assertEqual(support_evidence(obs, 'green cube')['status'], 'unknown')

    def test_translation_keeps_x_z_and_uses_bounded_y(self):
        poses = observation_translations([.6, .19, .2])
        self.assertEqual(len(poses), 1)
        self.assertAlmostEqual(poses[0][1], .09)
        self.assertEqual([poses[0][0], poses[0][2]], [.6, .2])

    def test_support_targeted_action_runs_even_with_known_held_center_and_stops_after_view(self):
        trial = PlacementViewTrial(SimpleNamespace(event=lambda *a, **k: None), 'support_active')
        calls = []
        def gain(label, code, contract):
            calls.append((label, code, contract))
            return measurement()
        trial.segment = gain
        result = trial.prepare_placement(measurement(status='unknown'), 'red cube', 'green cube')
        self.assertEqual(support_evidence(result, 'green cube')['status'], 'available')
        self.assertEqual(len(calls), 1)
        self.assertIn('inspect-support-green cube', calls[0][0])
        self.assertIn('check_holding', calls[0][1])
        self.assertEqual(calls[0][2]['preconditions'], [{'holding': 'red cube'}])

    def test_no_gain_or_holding_failure_cannot_lead_to_unbounded_or_further_actions(self):
        trial = PlacementViewTrial(SimpleNamespace(event=lambda *a, **k: None), 'support_active')
        calls = []
        def no_gain(*args):
            calls.append(args)
            return measurement(status='unknown')
        trial.segment = no_gain
        with self.assertRaisesRegex(ValueError, 'remains unobservable'):
            trial.prepare_placement(measurement(status='unknown'), 'red cube', 'green cube')
        self.assertEqual(len(calls), 2)
        calls.clear()
        def fail(*args):
            calls.append(args)
            raise RuntimeError('cancelled or holding failed')
        trial.segment = fail
        with self.assertRaises(RuntimeError):
            trial.prepare_placement(measurement(status='unknown'), 'red cube', 'green cube')
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
