"""Temporal protocol tests; fixture results are not robot performance."""
import time
import unittest
from unittest.mock import patch
import test_runtime as fixtures

until = fixtures.until


class VerificationWaitTests(unittest.TestCase):
    setUp = fixtures.RuntimeTests.setUp
    tearDown = fixtures.RuntimeTests.tearDown

    def pending(self, *_args):
        return dict(status='pending', reason='injected_incomplete_sensor_window')

    def start(self):
        return self.e.execute('phase', self.e.revision, 'phase', 'pick("red cube")',
                              dict(postcondition={'holding': 'red cube'}))

    def waiting(self):
        return any(e['kind'] == 'verification_wait_started' for e in self.e.events)

    def test_owner_keeps_stepping_until_evidence_is_ready_without_more_api_calls(self):
        def readiness(*_args):
            return dict(status='ready' if self.e.backend.ticks >= 55 else 'pending')
        self.e.backend.verification_readiness = readiness
        sid = self.start()
        until(self.waiting)
        before = self.e.backend.ticks
        self.assertIsNotNone(self.e.active)
        until(lambda: self.e.active is None)
        result = self.e.segments[sid]
        self.assertGreater(self.e.backend.ticks, before)
        self.assertEqual(result['effect'], 'verified')
        self.assertEqual(result['verification_wait']['status'], 'ready')
        self.assertFalse(result['cancel_requested'])  # An internal hold is not a user cancellation.
        self.assertEqual(self.e.used['calls'], 1)

    def test_complete_but_occluded_window_returns_unknown_without_waiting_for_success(self):
        self.e.backend.verification_readiness = lambda *_: dict(status='ready')
        self.e.backend.occluded = True
        sid = self.start()
        until(lambda: self.e.active is None)
        self.assertEqual(self.e.segments[sid]['execution'], 'completed')
        self.assertEqual(self.e.segments[sid]['effect'], 'unknown')
        self.assertFalse(self.waiting())

    def test_missing_sensor_stream_times_out_unknown_even_if_fixture_goal_is_true(self):
        self.e.backend.verification_readiness = self.pending
        with patch('agentic_cap.core.POST_ACTION_EVIDENCE_TIMEOUT_SECONDS', .15):
            sid = self.start()
            until(lambda: self.e.active is None)
        result = self.e.segments[sid]
        self.assertEqual(result['execution'], 'completed')
        self.assertEqual(result['effect'], 'unknown')
        self.assertEqual(result['verification_wait']['status'], 'timed_out')
        self.assertEqual(result['stage_verification']['status'], 'unknown')
        self.assertEqual(self.e.used['calls'], 1)

    def test_stop_interrupts_pending_verification(self):
        self.e.backend.verification_readiness = self.pending
        sid = self.start()
        until(self.waiting)
        started = time.monotonic()
        self.assertTrue(self.e.cancel()['stopped'])
        until(lambda: self.e.active is None)
        self.assertLess(time.monotonic()-started, .5)
        result = self.e.segments[sid]
        self.assertEqual(result['execution'], 'cancelled')
        self.assertEqual(result['effect'], 'unknown')
        self.assertEqual(result['feedback']['category'], 'cancelled')
        finished = [e for e in self.e.events if e['kind'] == 'verification_wait_finished']
        self.assertEqual(finished[-1]['result']['status'], 'cancelled')

    def test_goal_change_invalidates_pending_verification(self):
        self.e.backend.verification_readiness = self.pending
        sid = self.start()
        until(self.waiting)
        self.e.update_goal({'holding': 'green cube'})
        until(lambda: self.e.active is None)
        self.assertEqual(self.e.segments[sid]['execution'], 'cancelled')
        self.assertEqual(self.e.segments[sid]['stage_verification']['status'], 'unknown')
        self.assertEqual(self.e.goal, {'holding': 'green cube'})
        self.assertEqual(self.e.used['calls'], 1)

    def test_wall_budget_includes_verification_wait(self):
        self.e.backend.verification_readiness = self.pending
        self.e.limits['wall_seconds'] = .6
        sid = self.start()
        until(lambda: self.e.active is None)
        result = self.e.segments[sid]
        self.assertEqual(result['execution'], 'cancelled')
        self.assertEqual(result['feedback']['category'], 'budget_exhausted')
        self.assertEqual(result['effect'], 'unknown')

    def test_readiness_exception_cannot_bypass_temporal_validation(self):
        def broken(*_args):
            raise RuntimeError('sensor readiness unavailable')
        self.e.backend.verification_readiness = broken
        sid = self.start()
        until(lambda: self.e.active is None)
        self.assertEqual(self.e.segments[sid]['effect'], 'unknown')
        self.assertIsNone(self.e.active)
