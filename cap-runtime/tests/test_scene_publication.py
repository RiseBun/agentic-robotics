"""Atomic terminal scene publication; fixture evidence is not robot performance."""
import tempfile
import time
import unittest
from agentic_cap.core import Manager, StaleRevision
from agentic_cap.fixture import FixtureBackend
from test_runtime import until


class SceneFixture(FixtureBackend):
    def __init__(self, episode):
        super().__init__(episode)
        self.x = .4
        self.complete_window = False

    def capture(self):
        result = super().capture()
        result.update(captured_at=time.time(), evidence_ref=f'frame-{self.ticks}',
                      objects={'red cube': dict(status='visible', surface_median_xyz=[self.x, 0., .1])})
        return result

    def tools(self, segment):
        tools = super().tools(segment)
        pick = tools['pick']
        def move(name):
            result = pick(name)
            self.x = .5
            return result
        tools['pick'] = move
        return tools

    def verification_readiness(self, goals, snapshot):
        observed = snapshot['objects']['red cube']['surface_median_xyz'][0]
        return dict(status='ready' if observed == self.x and self.complete_window else 'pending')


class ScenePublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = Manager(self.temp.name, SceneFixture)
        state = self.manager.create('scene', 'cube_lifting', 1, {'holding': 'red cube'})
        self.e = self.manager.episodes[state['id']]
        until(lambda: self.e.snapshot and self.e.status == 'ready')

    def tearDown(self):
        self.e.close()
        self.temp.cleanup()

    def test_action_result_revision_is_published_before_segment_finished(self):
        original = self.e.revision
        sid = self.e.execute('move', original, 'pick', 'pick("red cube")')
        until(lambda: self.e.snapshot['objects']['red cube']['surface_median_xyz'][0] == .5)
        self.assertIsNotNone(self.e.active)
        self.assertEqual(self.e.revision, original)
        self.e.backend.complete_window = True
        until(lambda: self.e.state()['active_segment'] is None)
        state = self.e.state()
        segment = state['segments'][sid]
        self.assertEqual(segment['execution'], 'completed')
        self.assertEqual(segment['result_revision'], original+1)
        self.assertEqual(state['revision'], original+1)
        events = self.e.events
        change = next(i for i,e in enumerate(events) if e['kind']=='scene_changed')
        finished = next(i for i,e in enumerate(events) if e['kind']=='segment_finished')
        self.assertLess(change, finished)
        with self.assertRaises(StaleRevision):
            self.e.execute('old', original, 'late', 'pick("red cube")')
        time.sleep(.25)
        self.assertEqual(self.e.revision, original+1)
        self.assertEqual(self.e.used['calls'], 1)
        self.assertEqual(self.e.backend.reset_count, 1)

    def test_later_external_change_still_invalidates_new_decision(self):
        original = self.e.revision
        self.e.backend.x = .6
        until(lambda: self.e.revision > original)
        with self.assertRaises(StaleRevision):
            self.e.execute('late', original, 'old decision', 'pick("red cube")')
        self.assertEqual(self.e.used['calls'], 0)

    def test_small_drift_accumulates_against_material_baseline(self):
        original = self.e.revision
        self.e.backend.x = .411
        until(lambda: self.e.snapshot['objects']['red cube']['surface_median_xyz'][0] == .411)
        self.assertEqual(self.e.revision, original)
        self.e.backend.x = .422
        until(lambda: self.e.revision > original)
        self.assertEqual(self.e.revision, original+1)

    def test_cancel_during_wait_keeps_action_cancelled_and_publishes_latest_scene(self):
        original = self.e.revision
        sid = self.e.execute('cancel', original, 'pick', 'pick("red cube")')
        until(lambda: self.e.snapshot['objects']['red cube']['surface_median_xyz'][0] == .5)
        self.assertTrue(self.e.cancel()['stopped'])
        until(lambda: self.e.state()['active_segment'] is None)
        # The HTTP interface reads state under the episode lock. Reading the
        # worker's raw fields can observe active=None before result publication.
        state = self.e.state()
        segment = state['segments'][sid]
        self.assertEqual(segment['execution'], 'cancelled')
        self.assertEqual(segment['result_revision'], state['revision'])
        self.assertGreater(state['revision'], original)
        self.assertEqual(self.e.used['calls'], 1)
