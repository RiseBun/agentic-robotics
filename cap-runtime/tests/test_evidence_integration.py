"""Real episode/interpreter/event paths with deterministic sensor fixtures.

These checks validate the protocol, never physical task success.
"""
import copy
import io
import json
import tempfile
import threading
import time
import unittest
import zipfile
from types import SimpleNamespace

from agentic_cap.active_evidence import install_inspector
from agentic_cap.core import Manager
from agentic_cap.fixture import FixtureBackend
from agentic_cap.server import export_episode
from test_runtime import until


class EvidenceFixture(FixtureBackend):
    def __init__(self, episode):
        super().__init__(episode)
        self.xyz, self.orientation = [.5, 0., .2], [0., 0., 1., 0.]
        self.recover_at, self.drop_at = 1, None
        self.view_targets, self.holding_checks, self.releases = [], 0, 0
        self.fail_motion = False
        self.gate = None
        self.entered = threading.Event()
        self.reference = None
        self.inspection_sessions = {}
        self.held_hint = self.held = 'red cube'

    def initialize(self):
        super().initialize()
        self.episode.gripper = 0.

    @property
    def tick(self):
        return self.ticks

    def capture(self):
        visible = self.recover_at is not None and len(self.view_targets) >= self.recover_at
        support = dict(status='visible' if visible else 'unknown', surface_span_xyz=[.04] * 3,
                       surface_median_xyz=[.5, .1, .04], valid_depth_pixels=100)
        return dict(backend='fixture', tcp_xyz=self.xyz.copy(), tcp_wxyz=self.orientation.copy(),
                    joint_positions=self.q.copy(), gripper_joint_positions=[.02, .02],
                    control_step=self.ticks, captured_at=time.time(), evidence_ref=f'frame-{self.ticks}',
                    objects={'green cube': support, 'red cube': dict(status='visible')})

    def tools(self, segment):
        raw = super().tools(segment)

        def move(xyz, quat):
            self.entered.set()
            if self.gate:
                self.gate.wait(4)
            self.episode.checkpoint(segment)
            if self.fail_motion:
                return dict(status='failed', reason='fixture motion failure')
            raw['wait_motion'](4)
            self.episode.checkpoint(segment)
            self.xyz, self.orientation = list(xyz), list(quat)
            self.view_targets.append((list(xyz), list(quat)))
            if self.drop_at == len(self.view_targets):
                self.held = None
            return dict(status='reached')

        def holding(name):
            self.holding_checks += 1
            raw['wait_motion'](4)
            return dict(status='verified' if self.held == name else 'failed')

        def release():
            self.releases += 1
            self.placed = [self.held, 'green cube']
            self.held = None
            self.episode.gripper = 1.

        return install_inspector(self, segment, dict(
            goto_pose=move, check_holding=holding, open_gripper=release,
            close_gripper=lambda: None, get_execution_state=self.episode.observe))

    def verify(self, goal, snapshot):
        if 'on' in goal and snapshot.get('objects', {}).get('green cube', {}).get('status') != 'visible':
            return dict(status='unknown', reason='fixture support occlusion')
        return super().verify(goal, snapshot)


class EvidenceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = Manager(self.temp.name, EvidenceFixture)
        state = self.manager.create('create', 'cube_stack', 61,
                                    {'on': ['red cube', 'green cube'], 'empty_gripper': True})
        self.e = self.manager.episodes[state['id']]
        until(lambda: self.e.status == 'ready' and self.e.snapshot)
        self.b = self.e.backend

    def tearDown(self):
        if self.b.gate:
            self.b.gate.set()
        self.e.close()
        self.temp.cleanup()

    def run_code(self, code, request='segment'):
        sid = self.e.execute(request, self.e.revision, 'evidence', code)
        until(lambda: self.e.active is None)
        return self.e.segments[sid]

    def inspect(self, request='inspect'):
        return self.run_code('inspect_support("green cube", "red cube")', request)

    def test_available_has_no_motion(self):
        self.b.recover_at = 0
        until(lambda: self.e.observe()['objects']['green cube']['status'] == 'visible')
        result = self.inspect()
        self.assertEqual(result['evidence_acquisition']['status'], 'available')
        self.assertEqual(self.b.view_targets, [])

    def test_unknown_recovers_then_release_uses_new_frame(self):
        before = self.e.observe()['evidence_ref']
        result = self.inspect()
        self.assertEqual(result['execution'], 'completed')
        acquisition = result['evidence_acquisition']
        self.assertEqual(acquisition['status'], 'available')
        self.assertNotEqual(acquisition['evidence_ref'], before)
        self.assertEqual(acquisition['views_used'], 1)
        self.run_code('open_gripper()', 'release')
        self.assertEqual(self.b.releases, 1)
        self.assertEqual(self.b.reset_count, 1)

    def test_two_unknown_views_stop_and_block_motion_and_release(self):
        self.b.recover_at = None
        result = self.inspect()
        self.assertEqual(result['evidence_acquisition']['status'], 'unknown')
        self.assertEqual(result['execution'], 'failed')
        self.assertEqual(len(self.b.view_targets), 2)
        self.assertEqual(self.b.holding_checks, 3)
        for xyz, quat in self.b.view_targets:
            self.assertEqual([xyz[0], xyz[2]], [.5, .2])
            self.assertLessEqual(abs(xyz[1]), .22)
            self.assertEqual(quat, [0., 0., 1., 0.])
        self.inspect('repeat-inspect')
        self.assertEqual(len(self.b.view_targets), 2)
        blocked = self.run_code('goto_pose([.5, .1, .04], [0., 0., 1., 0.])\nopen_gripper()', 'blind')
        self.assertEqual(blocked['execution'], 'failed')
        self.assertEqual(len(self.b.view_targets), 2)
        self.run_code('open_gripper()', 'blind-release')
        self.assertEqual(self.b.releases, 0)

    def test_loss_of_holding_stops_before_second_view(self):
        self.b.recover_at, self.b.drop_at = None, 1
        result = self.inspect()
        self.assertEqual(result['evidence_acquisition']['status'], 'failed')
        self.assertEqual(len(self.b.view_targets), 1)
        self.assertEqual(self.b.holding_checks, 2)

    def test_failed_motion_stops_without_second_view_or_holding_check(self):
        self.b.fail_motion = True
        result = self.inspect()
        self.assertEqual(result['execution'], 'failed')
        self.assertEqual(self.b.view_targets, [])
        self.assertEqual(self.b.holding_checks, 1)

    def test_cancel_during_view_prevents_late_actions(self):
        self.b.gate = threading.Event()
        sid = self.e.execute('slow', self.e.revision, 'inspect',
                             'inspect_support("green cube", "red cube")\nopen_gripper()')
        self.assertTrue(self.b.entered.wait(2))
        self.assertTrue(self.e.cancel()['stopped'])
        until(lambda: self.e.active is None)
        self.b.gate.set()
        until(lambda: any(x['kind'] == 'evidence_acquisition_finished' for x in self.e.events))
        self.assertEqual(self.e.segments[sid]['execution'], 'cancelled')
        finished = next(x for x in self.e.events if x['kind'] == 'evidence_acquisition_finished')
        self.assertEqual(finished['segment_id'], sid)
        self.assertIn('after', finished)
        self.assertEqual(self.b.view_targets, [])
        self.assertEqual(self.b.releases, 0)

    def test_goal_change_during_view_invalidates_old_inspection(self):
        self.b.gate = threading.Event()
        old = self.e.revision
        self.e.execute('old', old, 'inspect', 'inspect_support("green cube", "red cube")\nopen_gripper()')
        self.assertTrue(self.b.entered.wait(2))
        self.e.update_goal({'holding': 'green cube'})
        until(lambda: self.e.active is None)
        self.b.gate.set()
        until(lambda: any(x['kind'] == 'evidence_acquisition_finished' for x in self.e.events))
        self.assertEqual(self.b.view_targets, [])
        self.assertEqual(self.b.releases, 0)
        with self.assertRaises(ValueError):
            self.e.execute('late', old, 'old placement', 'open_gripper()')

    def test_idempotency_does_not_reexecute_skill(self):
        result = self.inspect('same')
        sid = self.e.execute('same', result['revision'], 'evidence', 'inspect_support("green cube", "red cube")')
        self.assertEqual(sid, result['id'])
        self.assertEqual(len(self.b.view_targets), 1)

    def test_nested_api_calls_use_cumulative_budget(self):
        self.e.limits['calls'] = 2  # outer inspection + initial holding only
        result = self.inspect()
        self.assertEqual(result['execution'], 'cancelled')
        self.assertEqual(self.e.used['calls'], 2)
        self.assertEqual(self.b.view_targets, [])

    def test_physical_fixture_completion_under_occlusion_is_unknown(self):
        self.b.placed, self.b.held = ['red cube', 'green cube'], None
        self.b.recover_at = None
        self.assertEqual(self.e.finish(self.e.revision)['status'], 'unknown')

    def test_event_chain_roundtrips_through_export(self):
        self.inspect()
        self.e.close()
        with zipfile.ZipFile(io.BytesIO(export_episode(self.manager.root, self.e.id))) as pack:
            events = [json.loads(line) for line in pack.read(self.e.id + '/events.jsonl').decode().splitlines()]
        self.assertEqual([x['id'] for x in events], list(range(1, len(events)+1)))
        selected = [x for x in events if x['kind'] in {'evidence_acquisition_started', 'view_attempt', 'view_result', 'evidence_acquisition_finished'}]
        self.assertEqual([x['kind'] for x in selected], ['evidence_acquisition_started', 'view_attempt', 'view_result', 'evidence_acquisition_finished'])
        self.assertTrue(all(x['episode_id'] == self.e.id and x['segment_id'] for x in selected))
        view = selected[2]
        self.assertNotEqual(view['before']['evidence_ref'], view['after']['evidence_ref'])
        self.assertIn('motion_steps', view['used_delta'])


if __name__ == '__main__':
    unittest.main()
