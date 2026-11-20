import unittest
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np

from agentic_cap.cap_backend import CaPBackend
from experiments.holding_model_server import HoldingModelBackend


class HoldingModelDiagnosisTests(unittest.TestCase):
    def backend(self, mode='recoverable'):
        events = []
        e = SimpleNamespace(active='executing', runtime_alert=None,
                            event=lambda kind, **data: events.append(dict(kind=kind, **data)))
        b = object.__new__(HoldingModelBackend)
        b.episode, b.diagnostic_mode = e, mode
        b.release_scheduled_step = b.release_started_step = None
        b.release_applied_steps = 0
        b.red_mask_announced = False
        b.held_hint, b.holding_results, b.tick = 'red cube', {'red cube': 'verified'}, 0
        return b, events

    def test_fault_waits_for_public_verified_hold_and_idle_then_opens_only_thirty_steps(self):
        b, events = self.backend()
        commands = []
        def physics(_backend, _target, command):
            commands.append(command)
            b.tick += 1
        with patch.object(CaPBackend, 'step', physics):
            b.step([], 0.)
            self.assertIsNone(b.release_scheduled_step)
            b.episode.active = None
            b.holding_results['red cube'] = 'unknown'
            b.step([], 0.)
            self.assertIsNone(b.release_scheduled_step)
            b.holding_results['red cube'] = 'verified'
            b.step([], 1.)
            self.assertIsNone(b.release_scheduled_step)
            for _ in range(80): b.step([], 0.)
        self.assertEqual(sum(c == 1. for c in commands), 31)  # One desired open before arming, thirty injected.
        self.assertEqual(b.release_applied_steps, 30)
        self.assertEqual(commands[-1], 0.)
        self.assertEqual(sum(e['kind'] == 'diagnostic_actuator_fault_started' for e in events), 1)
        self.assertEqual(sum(e['kind'] == 'diagnostic_actuator_fault_finished' for e in events), 1)

    def test_sensor_loss_begins_after_hold_confirmation_and_latches_without_object_truth(self):
        b, events = self.backend('occluded')
        rgb = np.zeros((12,12,3), dtype=np.uint8)
        rgb[:,:,0] = 200
        depth = np.ones((12,12))
        with patch.object(CaPBackend, 'apply_camera_fault', lambda _b, r, d: (r,d,False)):
            self.assertFalse(b.apply_camera_fault(rgb, depth)[2])
            b.episode.runtime_alert = dict(hold_confirmed=False, event_id=9)
            self.assertFalse(b.apply_camera_fault(rgb, depth)[2])
            b.episode.runtime_alert['hold_confirmed'] = True
            self.assertTrue(b.apply_camera_fault(rgb, depth)[2])
            b.episode.runtime_alert = None
            self.assertTrue(b.apply_camera_fault(rgb, depth)[2])
        self.assertEqual(sum(e['kind'] == 'diagnostic_red_sensor_fault_started' for e in events), 1)


if __name__ == '__main__': unittest.main()
