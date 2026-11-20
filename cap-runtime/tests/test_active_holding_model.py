import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agentic_cap.cap_backend import CaPBackend
from agentic_cap.core import Halt
from experiments.active_holding_model_server import ActiveHoldingBackend


class ActiveHoldingModelTests(unittest.TestCase):
    def backend(self):
        events, calls = [], []
        b = object.__new__(ActiveHoldingBackend)
        b.episode = SimpleNamespace(active='next', gripper=0., cv=threading.Condition(),
            checkpoint=lambda segment: None, event=lambda kind, **data: events.append(dict(kind=kind, **data)))
        b.verified_segment, b.fault_segment = 'verified', None
        b.release_started_step, b.release_applied_steps, b.tick = None, 0, 0
        b.held_hint, b.holding_results = 'red cube', {'red cube': 'verified'}
        return b, events, calls

    def test_fault_only_during_new_active_segment_and_only_once(self):
        b, events, commands = self.backend()
        def physics(_b, _target, gripper):
            commands.append(gripper)
            b.tick += 1
        with patch.object(CaPBackend, 'step', physics):
            b.step([], 0.)
            self.assertIsNone(b.release_started_step)
            b.fault_segment = 'next'
            b.episode.active = None
            b.step([], 0.)
            self.assertIsNone(b.release_started_step)
            b.episode.active = 'next'
            for _ in range(50):
                b.step([], 0.)
        self.assertEqual(commands.count(1.), 30)
        self.assertEqual(commands[-1], 0.)
        self.assertEqual(sum(e['kind']=='diagnostic_actuator_fault_started' for e in events), 1)

    def test_delay_is_not_armed_by_unverified_or_same_segment_holding(self):
        for mode in ['unverified', 'same_segment', 'open']:
            b, events, calls = self.backend()
            if mode == 'unverified': b.holding_results['red cube'] = 'unknown'
            if mode == 'same_segment': b.verified_segment = 'next'
            if mode == 'open': b.episode.gripper = 1.
            with patch.object(CaPBackend, 'tools', lambda _b, _s: {'get_object_pose': lambda name: calls.append(name)}):
                b.tools({'id': 'next'})['get_object_pose']('green cube')
            self.assertEqual(calls, ['green cube'])
            self.assertIsNone(b.fault_segment)
            self.assertEqual(events, [])

    def test_cancellation_during_latency_never_calls_the_original_tool(self):
        b, events, calls = self.backend()
        checkpoints = 0
        def checkpoint(segment):
            nonlocal checkpoints
            checkpoints += 1
            if checkpoints > 1:
                raise Halt('cancelled', 'CANCELLED')
        b.episode.checkpoint = checkpoint
        with patch.object(CaPBackend, 'tools', lambda _b, _s: {'get_object_pose': lambda name: calls.append(name)}):
            with self.assertRaises(Halt):
                b.tools({'id': 'next'})['get_object_pose']('green cube')
        self.assertEqual(calls, [])
        self.assertEqual([e['kind'] for e in events], ['diagnostic_api_delay_started', 'diagnostic_api_delay_abandoned'])


if __name__ == '__main__':
    unittest.main()
