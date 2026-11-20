"""Runtime expiration must converge without the disconnected policy client."""
import tempfile
import threading
import time
import unittest

from agentic_cap.core import Manager
from agentic_cap.fixture import FixtureBackend
from test_runtime import until


class DeadlineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = Manager(self.temp.name, FixtureBackend)
        state = self.manager.create('create', 'cube_lifting', 1, {'holding': 'red cube'})
        self.e = self.manager.episodes[state['id']]
        until(lambda: self.e.status == 'ready')
        self.release = threading.Event()

    def tearDown(self):
        self.release.set()
        self.e.close()
        self.e.thread.join(2)
        self.temp.cleanup()

    def expire_soon(self):
        self.e.limits['wall_seconds'] = time.monotonic()-self.e.started+.25

    def check_terminal(self):
        until(lambda: self.e.status == 'closed')
        self.e.thread.join(1)
        self.assertFalse(self.e.thread.is_alive())
        state = self.e.state()
        self.assertEqual(state['termination_reason'], 'wall_budget_exhausted')
        self.assertLess(state['used']['wall_seconds']-state['limits']['wall_seconds'], .25)
        self.assertIsNone(self.e.active)
        self.assertFalse(self.e.motion)
        self.assertTrue(self.e.events[-1]['hold_confirmed'])
        self.assertEqual(self.e.events[-1]['kind'], 'episode_closed')
        before = self.e.state()['used']
        tick = self.e.backend.ticks
        time.sleep(.03)
        self.assertEqual(self.e.state()['used'], before)
        self.assertEqual(self.e.backend.ticks, tick)
        self.e.close()
        self.assertEqual(sum(e['kind'] == 'episode_closed' for e in self.e.events), 1)

    def test_no_client_requests_still_hold_close_and_release_physics(self):
        self.expire_soon()
        self.check_terminal()
        self.assertTrue(self.e.cancel()['stopped'])
        with self.assertRaises(ValueError):
            self.e.execute('late', self.e.revision, 'late', 'pick("red cube")')
        with self.assertRaises(ValueError):
            self.e.update_goal({'holding': 'green cube'})
        self.assertEqual(self.e.goal, {'holding': 'red cube'})

    def test_wall_expiration_during_motion_closes_without_dispatching_the_next_action(self):
        self.expire_soon()
        sid = self.e.execute('long', self.e.revision, 'long', 'wait_motion(200)\npick("red cube")')
        self.check_terminal()
        self.assertEqual(self.e.segments[sid]['execution'], 'cancelled')
        self.assertEqual(self.e.segments[sid]['feedback']['category'], 'budget_exhausted')
        self.assertEqual(self.e.used['calls'], 1)
        self.assertIsNone(self.e.backend.held)

    def test_uncooperative_perception_cannot_keep_task_or_late_code_alive(self):
        original = self.e.backend.tools
        def tools(segment):
            values = original(segment)
            values['slow'] = lambda: self.release.wait(3)
            return values
        self.e.backend.tools = tools
        self.expire_soon()
        sid = self.e.execute('slow', self.e.revision, 'slow', 'slow()\npick("red cube")')
        self.check_terminal()
        self.release.set()
        time.sleep(.04)
        self.assertEqual(self.e.used['calls'], 1)
        self.assertIsNone(self.e.backend.held)
        self.assertEqual(self.e.segments[sid]['execution'], 'cancelled')

    def test_uncooperative_tool_setup_is_also_cancellable(self):
        original = self.e.backend.tools
        def tools(segment):
            self.release.wait(3)
            return original(segment)
        self.e.backend.tools = tools
        self.expire_soon()
        sid = self.e.execute('setup', self.e.revision, 'setup', 'pick("red cube")')
        self.check_terminal()
        self.release.set()
        self.assertEqual(self.e.used['calls'], 0)
        self.assertEqual(self.e.segments[sid]['execution'], 'cancelled')
