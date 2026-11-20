import copy
import tempfile
import time
import unittest

from agentic_cap.core import Manager, StaleRevision, HoldPending
from agentic_cap.fixture import FixtureBackend
from agentic_cap.holding_watchdog import HoldingWatchdog


def until(fn, timeout=5):
    deadline = time.monotonic()+timeout
    while time.monotonic() < deadline:
        if fn(): return
        time.sleep(.01)
    raise AssertionError('Timed out')


class PublicWatcherTests(unittest.TestCase):
    def setUp(self):
        self.w = HoldingWatchdog('test')
        self.reference = {'objects': {'red cube': dict(status='visible', surface_median_xyz=[.5, 0, .05])}}
        self.now = time.time()
        self.w.arm('red cube', dict(episode_id='test', control_step=10, evidence_ref='frame-000010.npz'))

    def samples(self, state='lost'):
        rows = []
        for step in range(20, 46, 5):
            obj = dict(status='visible', surface_median_xyz=[.5, 0, .05 if state == 'lost' else .18])
            if state == 'hidden': obj = dict(status='unknown')
            rows.append(dict(episode_id='test', control_step=step, captured_at=self.now-(45-step)*.02,
                evidence_ref=f'frame-{step:06d}.npz', tcp_xyz=[.5, 0, .2], last_gripper_command='closed',
                objects={'red cube': obj}))
        return rows

    def check(self, rows, closed=True):
        return self.w.evaluate(self.reference, rows, rows[-1], closed)

    def test_verified_grasp_then_complete_later_contradiction_trips_once(self):
        rows = self.samples()
        report = self.check(rows)
        self.assertEqual(report['status'], 'violated')
        self.assertEqual(report['evidence']['state'], 'violated')
        self.assertEqual(len(report['evidence_refs']), 6)
        self.assertEqual(self.check(rows)['reason'], 'already_interrupted')
        self.assertFalse(self.w.arm('red cube', dict(episode_id='test', control_step=10)))

    def test_still_held_and_occluded_are_not_loss(self):
        self.assertEqual(self.check(self.samples('held'))['status'], 'supported')
        rows = self.samples('hidden')
        for row in rows: row['control_step'] += 30
        self.assertEqual(self.check(rows)['status'], 'unknown')
        self.assertFalse(self.w.tripped)

    def test_single_frame_or_gapped_window_cannot_prove_loss(self):
        rows = self.samples()
        self.assertEqual(self.check(rows[-1:])['status'], 'unknown')
        self.w.last_step = 10
        self.assertEqual(self.check([rows[0], rows[1], rows[-1]])['reason'], 'incoherent_or_gapped_public_window')

    def test_stale_future_mixed_episode_or_command_window_is_unknown(self):
        for mutation in ['stale', 'future', 'episode', 'release', 'out_of_order']:
            with self.subTest(mutation=mutation):
                self.w.last_step = 10
                rows = self.samples()
                if mutation == 'stale': rows[-1]['captured_at'] -= 5
                if mutation == 'future': rows[-1]['captured_at'] += 5
                if mutation == 'episode': rows[1]['episode_id'] = 'another'
                if mutation == 'release': rows[1]['last_gripper_command'] = 'open'
                if mutation == 'out_of_order': rows[1]['control_step'] = rows[0]['control_step']
                self.assertEqual(self.check(rows)['status'], 'unknown')
                self.assertFalse(self.w.tripped)

    def test_release_disarms_and_old_window_cannot_rearm_itself(self):
        rows = self.samples()
        self.assertEqual(self.check(rows, False)['reason'], 'commanded_release')
        self.assertEqual(self.check(rows)['reason'], 'no_verified_grasp')

    def test_repeated_verified_check_does_not_reset_post_arm_window(self):
        self.w.arm('red cube', dict(episode_id='test', control_step=44))
        self.assertEqual(self.w.arm_record['control_step'], 10)
        self.assertEqual(self.check(self.samples())['status'], 'violated')

    def test_missing_reference_and_wrong_arm_identity_cannot_prove_loss(self):
        self.w.disarm()
        self.assertFalse(self.w.arm('red cube', dict(episode_id='wrong', control_step=10)))
        self.w.arm('red cube', dict(episode_id='test', control_step=10))
        self.reference = None
        self.assertEqual(self.check(self.samples())['reason'], 'missing_public_reference')


class MonitorFixture(FixtureBackend):
    def __init__(self, episode):
        super().__init__(episode)
        self.report = dict(status='inactive')

    def poll_holding_watchdog(self, snapshot):
        return copy.deepcopy(self.report)


class IndependentInterruptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        manager = Manager(self.temp.name, MonitorFixture)
        state = manager.create('monitor', 'cube_lifting', 1, {'holding': 'red cube'})
        self.e = manager.episodes[state['id']]
        until(lambda: self.e.status == 'ready' and self.e.snapshot)

    def tearDown(self):
        self.e.close()
        self.temp.cleanup()

    def signal(self):
        self.e.backend.report = dict(status='violated', object_name='red cube',
            arm=dict(control_step=10), evidence_ref='fixture', reason='public test contradiction')

    def test_monitor_during_motion_cancels_old_code_and_owner_confirms_hold(self):
        e = self.e
        with e.lock: e.gripper = 0
        revision = e.revision
        sid = e.execute('motion', revision, 'long phase', 'wait_motion(200)\npick("red cube")')
        until(lambda: any(x['kind'] == 'api_started' for x in e.events))
        self.signal()
        until(lambda: e.runtime_alert is not None and e.active is None and e.hold_ack >= e.runtime_alert['hold_serial'])
        segment = e.segments[sid]
        self.assertEqual(segment['execution'], 'cancelled')
        self.assertEqual(segment['feedback']['category'], 'public_holding_loss')
        self.assertEqual(segment['feedback']['evidence']['status'], 'violated')
        self.assertEqual(e.gripper, 0)
        self.assertFalse(any(x['kind'] == 'api_started' and x['name'] == 'pick' for x in e.events))
        self.assertEqual(e.backend.reset_count, 1)
        self.assertTrue(any(x['kind'] == 'hold_confirmed' and x['hold_serial'] >= e.runtime_alert['hold_serial'] for x in e.events))
        self.assertTrue(e.runtime_alert['hold_confirmed'])
        self.assertGreaterEqual(e.runtime_alert['hold_latency_seconds'], 0)
        time.sleep(.25)
        self.assertEqual(sum(x['kind'] == 'public_holding_contradicted' for x in e.events), 1)
        with self.assertRaises(StaleRevision): e.execute('late', revision, 'old placement', 'pick("red cube")')

    def test_idle_monitor_invalidates_inflight_model_without_running_a_segment(self):
        e = self.e
        old = e.revision
        self.signal()
        until(lambda: e.runtime_alert is not None and e.hold_ack >= e.runtime_alert['hold_serial'])
        self.assertGreater(e.revision, old)
        self.assertIsNone(e.active)
        self.assertEqual(e.observe()['runtime_alert']['code'], 'PUBLIC_HOLDING_LOSS')
        with self.assertRaises(StaleRevision): e.decision('late-model', old)
        self.assertNotEqual(e.finish(e.revision)['status'], 'verified')

    def test_unknown_monitor_report_does_not_interrupt_or_change_revision(self):
        e = self.e
        old = e.revision
        e.backend.report = dict(status='unknown', reason='object_not_visible')
        time.sleep(.3)
        self.assertEqual(e.revision, old)
        self.assertIsNone(e.runtime_alert)

    def test_no_new_decision_action_or_finish_before_owner_confirms_protective_hold(self):
        e = self.e
        with e.lock:
            e.interrupt_public_hold(dict(status='violated', object_name='red cube', arm=dict(control_step=10)))
            self.assertFalse(e.runtime_alert['hold_confirmed'])
            used = dict(e.used)
            with self.assertRaises(HoldPending): e.decision('too-early', e.revision)
            with self.assertRaises(HoldPending): e.execute('too-early', e.revision, 'late', 'pick("red cube")')
            self.assertEqual(e.used, used)
            self.assertEqual(e.finish(e.revision)['status'], 'unknown')
        until(lambda: e.runtime_alert['hold_confirmed'])


if __name__ == '__main__': unittest.main()
