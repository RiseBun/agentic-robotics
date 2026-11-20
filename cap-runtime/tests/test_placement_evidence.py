"""Post-placement protocol through Episode, interpreter and real public verifier.

The sensors and physical state here are fixtures, not robot success samples.
"""
import copy
import io
import json
import tempfile
import threading
import time
import unittest
import zipfile
from collections import deque

from agentic_cap.active_evidence import install_inspector
from agentic_cap.cap_backend import CaPBackend
from agentic_cap.core import Manager
from agentic_cap.fixture import FixtureBackend
from agentic_cap.placement_evidence import empty_gripper_evidence
from agentic_cap.server import export_episode
from test_runtime import until


def cube(center):
    return dict(status='visible', surface_median_xyz=list(center),
                surface_q05_xyz=[v-.02 for v in center], surface_q95_xyz=[v+.02 for v in center],
                surface_span_xyz=[.04]*3, valid_depth_pixels=100)


class PlacementFixture(FixtureBackend):
    _verification_samples = CaPBackend._verification_samples
    verification_readiness = CaPBackend.verification_readiness
    verify = CaPBackend.verify

    def __init__(self, e):
        super().__init__(e)
        self.xyz, self.quat = [.5, 0., .24], [0., 0., 1., 0.]
        self.last_action_tick = 0
        self.reference, self.history = None, deque(maxlen=40)
        self.inspection_sessions = {}
        self.held_hint = self.held = 'red cube'
        self.releases, self.grasps, self.lateral = 0, 0, []
        self.recover_at, self.close_after_view = 1, None
        self.missing_aperture, self.occlude_below, self.fail_motion = False, None, False
        self.gate, self.entered = None, threading.Event()
        self.correct = True

    @property
    def tick(self):
        return self.ticks

    def initialize(self):
        super().initialize()
        self.episode.gripper = 0.

    def capture(self):
        hidden = (self.releases and (self.recover_at is None or len(self.lateral) < self.recover_at))
        hidden = hidden or (self.occlude_below is not None and self.xyz[2] < self.occlude_below)
        red = cube([.5 if self.correct else .65, 0., .06 if self.releases else .10])
        green = dict(status='unknown') if hidden else cube([.5, 0., .02])
        width = [.04, -.04] if self.episode.gripper > .5 else [.02, -.02]
        if self.missing_aperture:
            width = []
        state = dict(backend='fixture', tcp_xyz=self.xyz.copy(), tcp_wxyz=self.quat.copy(),
                     joint_positions=self.q.copy(), gripper_joint_positions=width,
                     last_gripper_command='open' if self.episode.gripper > .5 else 'closed',
                     control_step=self.tick, captured_at=time.time(), evidence_ref=f'frame-{self.tick}',
                     objects={'red cube': red, 'green cube': green})
        self.history.append(copy.deepcopy(state))
        if self.reference is None:
            self.reference = copy.deepcopy(state)
        return state

    def tools(self, segment):
        wait_motion = super().tools(segment)['wait_motion']

        def move(xyz, quat):
            lateral = self.releases and abs(xyz[1]-self.xyz[1]) > .05
            if lateral:
                self.entered.set()
                if self.gate:
                    self.gate.wait(4)
            self.episode.checkpoint(segment)
            if self.fail_motion and lateral:
                return dict(status='failed', reason='injected motion failure')
            wait_motion(4)
            self.xyz, self.quat = list(xyz), list(quat)
            self.last_action_tick = self.tick
            if lateral:
                self.lateral.append((list(xyz), list(quat)))
                if len(self.lateral) == self.close_after_view:
                    self.episode.gripper = 0.
            return dict(status='reached')

        def release():
            self.releases += 1
            self.episode.gripper = 1.
            self.held = None
            self.last_action_tick = self.tick

        def grasp():
            self.grasps += 1
            self.episode.gripper = 0.

        return install_inspector(self, segment, dict(
            goto_pose=move, open_gripper=release, close_gripper=grasp,
            check_holding=lambda name: dict(status='verified' if self.held == name else 'failed'),
            get_execution_state=self.episode.observe))


class PlacementEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = Manager(self.temp.name, PlacementFixture)
        state = self.manager.create('create', 'cube_stack', 63,
                                    dict(on=['red cube', 'green cube'], empty_gripper=True))
        self.e = self.manager.episodes[state['id']]
        until(lambda: self.e.status == 'ready' and self.e.snapshot)
        self.b = self.e.backend

    def tearDown(self):
        if self.b.gate:
            self.b.gate.set()
        self.e.close()
        self.temp.cleanup()

    def run_code(self, code, request='segment'):
        sid = self.e.execute(request, self.e.revision, 'placement evidence', code)
        until(lambda: self.e.active is None, timeout=8)
        return self.e.segments[sid]

    def release_and_retreat(self):
        self.run_code('open_gripper()\ngoto_pose([.5, 0., .36], [0., 0., 1., 0.])', 'release')
        self.assertEqual(self.b.releases, 1)

    def inspect(self, request='inspect'):
        return self.run_code('inspect_placement("red cube", "green cube")', request)

    def test_visible_verified_relation_needs_no_motion(self):
        self.b.recover_at = 0
        self.release_and_retreat()
        result = self.inspect()
        self.assertEqual(result['evidence_acquisition']['status'], 'verified')
        self.assertEqual(self.b.lateral, [])

    def test_unknown_to_verified_uses_post_motion_window_and_final_runtime_verification(self):
        self.release_and_retreat()
        old = self.e.observe()['evidence_ref']
        self.assertEqual(self.e.finish(self.e.revision)['status'], 'unknown')
        result = self.inspect()
        self.assertEqual(result['execution'], 'completed')
        self.assertEqual(result['goal_verification']['status'], 'verified')
        acquired = result['evidence_acquisition']
        self.assertEqual(acquired['status'], 'verified')
        self.assertNotEqual(acquired['evidence_ref'], old)
        self.assertEqual(acquired['views_used'], 1)
        self.assertEqual(self.e.finish(self.e.revision)['status'], 'verified')
        self.assertEqual(self.b.reset_count, 1)
        self.assertEqual((self.b.releases, self.b.grasps), (1, 0))

    def test_persistent_occlusion_exhausts_two_views_without_repeating(self):
        self.b.recover_at = None
        self.release_and_retreat()
        result = self.inspect()
        self.assertEqual(result['evidence_acquisition']['status'], 'unknown')
        self.assertEqual(result['goal_verification']['status'], 'unknown')
        self.assertEqual(len(self.b.lateral), 2)
        self.inspect('repeat')
        self.assertEqual(len(self.b.lateral), 2)
        self.assertEqual(self.b.releases, 1)
        for xyz, quat in self.b.lateral:
            self.assertEqual([xyz[0], xyz[2]], [.5, .36])
            self.assertLessEqual(abs(xyz[1]), .22)
            self.assertEqual(quat, [0., 0., 1., 0.])

    def test_holding_object_cannot_use_post_release_skill(self):
        result = self.inspect()
        self.assertEqual(result['evidence_acquisition']['reason'], 'empty_gripper_unverified')
        self.assertEqual(self.b.lateral, [])
        self.assertEqual(self.b.releases, 0)

    def test_missing_aperture_prevents_movement(self):
        self.release_and_retreat()
        self.b.missing_aperture = True
        until(lambda: self.e.observe().get('gripper_joint_positions') == [])
        self.assertEqual(self.inspect()['evidence_acquisition']['status'], 'unknown')
        self.assertEqual(self.b.lateral, [])

    def test_release_without_retreat_cannot_move_laterally(self):
        self.run_code('open_gripper()', 'release-only')
        self.assertEqual(self.inspect()['evidence_acquisition']['reason'], 'vertical_retreat_not_confirmed')
        self.assertEqual(self.b.lateral, [])

    def test_loss_of_open_gripper_stops_after_first_view(self):
        self.b.recover_at, self.b.close_after_view = None, 1
        self.release_and_retreat()
        result = self.inspect()
        self.assertEqual(result['evidence_acquisition']['status'], 'unknown')
        self.assertEqual(len(self.b.lateral), 1)

    def test_goal_change_even_back_to_same_relation_invalidates_release(self):
        self.release_and_retreat()
        goal = copy.deepcopy(self.e.goal)
        self.e.update_goal(dict(holding='green cube'))
        self.e.update_goal(goal)
        result = self.inspect()
        self.assertEqual(result['evidence_acquisition']['reason'], 'release_superseded_by_goal_change')
        self.assertEqual(self.b.lateral, [])

    def start_blocked_view(self):
        self.release_and_retreat()
        self.b.gate = threading.Event()
        sid = self.e.execute('slow', self.e.revision, 'inspect',
                             'inspect_placement("red cube", "green cube")\nopen_gripper()')
        self.assertTrue(self.b.entered.wait(3))
        return sid

    def test_cancel_prevents_late_view_and_uses_original_segment_id(self):
        sid = self.start_blocked_view()
        self.assertTrue(self.e.cancel()['stopped'])
        until(lambda: self.e.active is None)
        self.b.gate.set()
        until(lambda: any(x['kind'] == 'evidence_acquisition_finished' for x in self.e.events))
        self.assertEqual(self.e.segments[sid]['execution'], 'cancelled')
        self.assertEqual(self.b.lateral, [])
        self.assertEqual(self.b.releases, 1)
        finished = next(x for x in self.e.events if x['kind'] == 'evidence_acquisition_finished')
        self.assertEqual(finished['segment_id'], sid)

    def test_goal_change_during_view_invalidates_old_actions(self):
        sid = self.start_blocked_view()
        self.e.update_goal(dict(holding='green cube'))
        until(lambda: self.e.active is None)
        self.b.gate.set()
        until(lambda: any(x['kind'] == 'evidence_acquisition_finished' for x in self.e.events))
        self.assertEqual(self.e.segments[sid]['execution'], 'cancelled')
        self.assertEqual(self.b.lateral, [])
        self.assertEqual(self.b.releases, 1)

    def test_budget_counts_outer_skill_and_inner_motion(self):
        self.release_and_retreat()
        self.e.limits['calls'] = self.e.used['calls'] + 1
        self.assertEqual(self.inspect()['execution'], 'cancelled')
        self.assertEqual(self.b.lateral, [])

    def test_idempotency_does_not_repeat_a_view(self):
        self.release_and_retreat()
        result = self.inspect('same')
        sid = self.e.execute('same', result['revision'], 'placement evidence',
                             'inspect_placement("red cube", "green cube")')
        self.assertEqual(sid, result['id'])
        self.assertEqual(len(self.b.lateral), 1)

    def test_descent_cannot_release_using_a_pre_motion_visible_frame(self):
        self.b.occlude_below = .10
        result = self.run_code('goto_pose([.5, 0., .08], [0., 0., 1., 0.])\nopen_gripper()', 'descend')
        self.assertEqual(result['execution'], 'failed')
        self.assertEqual(self.b.releases, 0)
        gate = next(x for x in self.e.events if x['kind'] == 'placement_release_checked')
        self.assertFalse(gate['allowed'])
        self.assertGreater(gate['evidence']['control_step'], self.b.last_action_tick)
        self.assertEqual(self.inspect()['evidence_acquisition']['reason'], 'empty_gripper_unverified')

    def test_motion_failure_does_not_try_a_second_view(self):
        self.release_and_retreat()
        self.b.fail_motion = True
        self.assertEqual(self.inspect()['execution'], 'failed')
        attempts = [x for x in self.e.events if x['kind'] == 'view_attempt']
        self.assertEqual(len(attempts), 1)
        self.assertEqual(self.b.lateral, [])

    def descend_until_release_blocked(self):
        self.b.holding_results = {'red cube': 'verified'}
        self.b.occlude_below = .12
        result = self.run_code('goto_pose([.5, 0., .10], [0., 0., 1., 0.])\nopen_gripper()', 'descend')
        self.assertEqual(result['execution'], 'failed')
        self.assertEqual(self.b.releases, 0)
        self.assertIsNotNone(self.b.safe_retreat)
        return result

    def test_recorded_vertical_retreat_is_allowed_without_approving_release(self):
        blocked = self.descend_until_release_blocked()
        self.assertEqual(blocked['feedback']['evidence']['recovery']['status'], 'available')
        result = self.run_code('goto_pose([.5, 0., .24], [0., 0., 1., 0.])', 'retreat')
        self.assertEqual(result['execution'], 'completed')
        self.assertAlmostEqual(self.b.xyz[2], .24)
        self.assertEqual(self.b.releases, 0)
        self.assertIsNone(self.b.safe_retreat)
        gate = next(x for x in self.e.events if x['kind'] == 'placement_retreat_checked')
        self.assertTrue(gate['allowed'])

    def test_state_read_after_motion_waits_for_a_post_motion_measured_frame(self):
        self.b.holding_results = {'red cube': 'verified'}
        result = self.run_code('goto_pose([.5, 0., .14], [0., 0., 1., 0.])\ns=get_execution_state()', 'fresh-state')
        self.assertEqual(result['execution'], 'completed')
        state = next(e['result'] for e in self.e.events if e['kind'] == 'api_result'
                     and e.get('name') == 'get_execution_state')
        self.assertEqual(state['tcp_xyz'], [.5, 0., .14])
        self.assertGreater(state['control_step'], self.b.last_action_tick)

    def test_retreat_exception_does_not_allow_lateral_motion_or_higher_target(self):
        self.descend_until_release_blocked()
        for index, code in enumerate([
                'goto_pose([.55, 0., .24], [0., 0., 1., 0.])',
                'goto_pose([.5, 0., .40], [0., 0., 1., 0.])',
                'goto_pose([.5, 0., .06], [0., 0., 1., 0.])',
                'goto_pose([.5, 0., .24], [1., 0., 0., 0.])']):
            result = self.run_code(code, f'invalid-retreat-{index}')
            self.assertEqual(result['execution'], 'failed')
            self.assertEqual(self.b.xyz, [.5, 0., .10])
        self.assertEqual(self.b.releases, 0)

    def test_goal_change_invalidates_the_recorded_descent(self):
        self.descend_until_release_blocked()
        goal = copy.deepcopy(self.e.goal)
        self.e.update_goal(dict(holding='green cube'))
        self.e.update_goal(goal)
        result = self.run_code('goto_pose([.5, 0., .24], [0., 0., 1., 0.])', 'old-retreat')
        self.assertEqual(result['execution'], 'failed')
        self.assertEqual(self.b.xyz, [.5, 0., .10])

    def test_new_close_command_invalidates_the_recorded_descent(self):
        self.descend_until_release_blocked()
        self.run_code('close_gripper()', 'new-close')
        self.assertIsNone(self.b.safe_retreat)
        result = self.run_code('goto_pose([.5, 0., .24], [0., 0., 1., 0.])', 'old-retreat')
        self.assertEqual(result['execution'], 'failed')

    def test_support_inspection_does_not_shove_laterally_after_a_blocked_descent(self):
        self.descend_until_release_blocked()
        result = self.run_code('inspect_support("green cube", "red cube")', 'low-inspect')
        evidence = result['evidence_acquisition']
        self.assertEqual(evidence['reason'], 'retract_before_lateral_inspection')
        self.assertEqual(evidence['views_used'], 0)
        self.assertEqual(self.b.xyz, [.5, 0., .10])
        self.run_code('goto_pose([.5, 0., .24], [0., 0., 1., 0.])', 'retreat')
        self.b.occlude_below = 1.  # Remains unknown even at the restored height.
        until(lambda: self.e.observe()['objects']['green cube']['status'] == 'unknown')
        result = self.run_code('inspect_support("green cube", "red cube")', 'high-inspect')
        self.assertEqual(result['evidence_acquisition']['views_used'], 2)
        self.assertTrue(all(xyz[2] == .24 for xyz, _ in self.b.lateral))

    def test_recovered_view_can_disprove_the_goal(self):
        self.b.correct = False
        self.release_and_retreat()
        result = self.inspect()
        self.assertEqual(result['evidence_acquisition']['status'], 'failed')
        self.assertEqual(result['goal_verification']['status'], 'failed')

    def test_event_chain_exports_fresh_observations_cost_and_release_link(self):
        self.release_and_retreat()
        self.inspect()
        self.e.close()
        with zipfile.ZipFile(io.BytesIO(export_episode(self.manager.root, self.e.id))) as pack:
            events = [json.loads(x) for x in pack.read(self.e.id+'/events.jsonl').decode().splitlines()]
        self.assertEqual([x['id'] for x in events], list(range(1, len(events)+1)))
        release = next(x for x in events if x['kind'] == 'placement_released')
        view = next(x for x in events if x['kind'] == 'view_result')
        self.assertEqual(view['release_id'], release['release_id'])
        self.assertIn('motion_steps', view['used_delta'])
        self.assertEqual(view['after']['status'], 'verified')


class EmptyGripperEvidenceTests(unittest.TestCase):
    def test_old_or_nonfinite_measurement_cannot_confirm_empty(self):
        snapshot = dict(captured_at=time.time(), last_gripper_command='open',
                        gripper_joint_positions=[.04, -.04])
        self.assertEqual(empty_gripper_evidence(snapshot)['status'], 'verified')
        self.assertEqual(empty_gripper_evidence(dict(snapshot, captured_at=time.time()-2))['status'], 'unknown')
        self.assertEqual(empty_gripper_evidence(dict(snapshot, gripper_joint_positions=[float('nan'), .04]))['status'], 'unknown')
        self.assertEqual(empty_gripper_evidence(dict(snapshot, last_gripper_command='closed'))['status'], 'unknown')


if __name__ == '__main__':
    unittest.main()
