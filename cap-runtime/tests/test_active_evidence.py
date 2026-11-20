import unittest
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

from agentic_cap.active_evidence import install_inspector, observation_targets, support_evidence
from agentic_cap.cap_backend import CaPBackend
from agentic_cap.feedback import EffectError


def snapshot(status='unknown', spans=(.035, .036, .037), y=.1):
    obj = {'status': status}
    if status == 'visible':
        obj.update(surface_span_xyz=list(spans), valid_depth_pixels=100, surface_median_xyz=[.5, 0., .04])
    return {'evidence_ref': 'frame-1.npz', 'captured_at': time.time(), 'tcp_xyz': [.5, y, .2],
            'tcp_wxyz': [0., 0., 1., 0.],
            'objects': {'green cube': obj, 'red cube': {'status': 'visible'}}}


class ActiveEvidenceTests(unittest.TestCase):
    def recovery_tools(self, held_hint=None, close_error=None):
        current, calls, events = snapshot(), [], []
        episode = SimpleNamespace(
            goal={'on': ['red cube', 'green cube'], 'empty_gripper': True},
            gripper=0.0, lock=threading.RLock(), used={'calls': 0}, limits={'calls': 40},
            observe=lambda: current.copy(), checkpoint=lambda segment: None,
            event=lambda kind, **data: events.append({'kind': kind, **data}),
        )
        session = {'tried': [[.5, .1, .2]], 'blocked': True}
        backend = SimpleNamespace(episode=episode, held_hint=held_hint,
            holding_results={'red cube': 'verified' if held_hint else 'failed'},
            reference={}, inspection_sessions={('green cube', 'red cube'): session})

        def close():
            calls.append('close')
            if close_error:
                raise close_error
            return {'status': 'completed'}

        tools = install_inspector(backend, {'id': 'segment', 'revision': 1}, {
            'close_gripper': close,
            'open_gripper': lambda: calls.append('open') or {'status': 'completed'},
        })
        return tools, backend, session, calls, events

    def test_new_grasp_invalidates_old_failed_holding_before_release(self):
        tools, backend, session, calls, events = self.recovery_tools()
        tools['close_gripper']()
        with self.assertRaises(EffectError):
            tools['open_gripper']()
        self.assertEqual(calls, ['close'])
        self.assertNotIn('red cube', backend.holding_results)
        self.assertEqual(len(session['tried']), 1)
        self.assertTrue(session['blocked'])
        self.assertTrue(any(e['kind'] == 'holding_evidence_invalidated' for e in events))

    def test_failed_close_cannot_reuse_prior_empty_grasp_evidence(self):
        tools, backend, _, calls, _ = self.recovery_tools(close_error=RuntimeError('actuator timeout'))
        with self.assertRaisesRegex(RuntimeError, 'actuator timeout'):
            tools['close_gripper']()
        with self.assertRaises(EffectError):
            tools['open_gripper']()
        self.assertEqual(calls, ['close'])
        self.assertNotIn('red cube', backend.holding_results)

    def test_failed_grasp_can_open_for_recovery_without_new_close(self):
        tools, _, _, calls, _ = self.recovery_tools()
        tools['open_gripper']()
        self.assertEqual(calls, ['open'])

    def test_redundant_close_preserves_verified_identity_and_view_budget(self):
        tools, backend, session, calls, _ = self.recovery_tools(held_hint='red cube')
        tools['close_gripper']()
        with self.assertRaises(EffectError):
            tools['open_gripper']()
        self.assertEqual(calls, ['close'])
        self.assertEqual(backend.held_hint, 'red cube')
        self.assertEqual(backend.holding_results['red cube'], 'verified')
        self.assertEqual(len(session['tried']), 1)
        self.assertTrue(session['blocked'])

    def test_missing_support_is_unknown(self):
        result = support_evidence(snapshot(), 'green cube')
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['reason'], 'support_not_visible')

    def test_visible_and_truncated_support_are_distinct(self):
        self.assertEqual(support_evidence(snapshot('visible'), 'green cube')['status'], 'available')
        result = support_evidence(snapshot('visible', (.01, .011, .04)), 'green cube')
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['reason'], 'support_horizontal_extent_truncated')

    def test_reference_scale_prevents_thin_patch_passing(self):
        result = support_evidence(snapshot('visible', (.01, .011, .012)), 'green cube',
                                  {'objects': {'green cube': {'surface_span_xyz': [.04, .04, .04]}}})
        self.assertEqual(result['status'], 'unknown')

    def test_stale_public_frame_is_unknown(self):
        frame = snapshot('visible')
        frame['captured_at'] = time.time() - 2
        self.assertEqual(support_evidence(frame, 'green cube')['reason'], 'stale_or_missing_observation')

    def test_targets_are_bounded_and_preserve_other_axes(self):
        targets = observation_targets([.5, .19, .2])
        self.assertEqual(len(targets), 1)
        self.assertAlmostEqual(targets[0][1], .09)
        self.assertEqual([targets[0][0], targets[0][2]], [.5, .2])

    def test_at_most_two_targets(self):
        self.assertLessEqual(len(observation_targets([.5, 0., .2])), 2)

    def test_runtime_skill_recovers_support_and_guards_release(self):
        current = snapshot('unknown')
        current.update(control_step=0, captured_at=time.time())
        events = []
        episode = SimpleNamespace(
            goal={'on': ['red cube', 'green cube'], 'empty_gripper': True},
            gripper=0.0, cv=threading.Condition(),
            lock=threading.RLock(), used={'calls': 0}, limits={'calls': 40},
            observe=lambda: current.copy(),
            checkpoint=lambda segment: None,
            event=lambda kind, **data: events.append({'kind': kind, **data}),
        )
        backend = object.__new__(CaPBackend)
        backend.episode = episode
        backend.active_evidence_enabled = True
        backend.api = SimpleNamespace(functions=lambda: {
            'goto_pose': lambda xyz, q: current.update(
                control_step=current['control_step'] + 1, captured_at=time.time(),
                objects={'green cube': {'status': 'visible', 'surface_span_xyz': [.035, .036, .037], 'surface_median_xyz': [.5, 0., .04], 'valid_depth_pixels': 100},
                         'red cube': {'status': 'visible'}}),
            'open_gripper': lambda: {'status': 'verified'},
        })
        backend.encoder = [0.] * 7
        backend.reference = {'objects': {'red cube': {
            'status': 'visible', 'surface_median_xyz': [0., 0., 0.],
        }}}
        backend.history = [dict(
            control_step=i, tcp_xyz=[0., 0., .1], tcp_wxyz=[0., 0., 1., 0.],
            last_gripper_command='closed',
            objects={'red cube': {
                'status': 'visible', 'surface_median_xyz': [0., 0., .1],
                'surface_q05_xyz': [-.01, -.01, .1],
                'surface_q95_xyz': [.01, .01, .1],
            }}) for i in range(21)]
        backend.tick = 0
        backend.held_hint = 'red cube'
        # This fixture supplies a synthetic motion implementation to isolate
        # inspector/release integration; TCP acceptance has its own owner tests.
        with patch('agentic_cap.cartesian_motion.install_cartesian_motion',
                   side_effect=lambda _backend, _segment, _api, functions: functions):
            tools = backend.tools({'revision': 1, 'cancel_requested': False})
        with self.assertRaises(EffectError):
            tools['open_gripper']()
        result = tools['inspect_support']('green cube', 'red cube')
        self.assertEqual(result['status'], 'available')
        self.assertEqual(result['views_used'], 1)
        self.assertTrue(any(e['kind'] == 'evidence_acquisition_finished' for e in events))
        # The same skill's evidence now permits the release guard.
        self.assertEqual(tools['open_gripper']()['status'], 'verified')


if __name__ == '__main__':
    unittest.main()
