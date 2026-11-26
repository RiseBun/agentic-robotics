"""Unified state/task/skill execution tests. Fixture evidence is not robot success."""
import copy
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

from agentic_cap.core import AssistancePending, DependenciesChanged, HoldPending, Manager
from agentic_cap.server import serve
from agentic_cap.fixture import FixtureBackend
from agentic_cap.skills import validate_skill
from test_runtime import until


class SystemFixture(FixtureBackend):
    def __init__(self, episode):
        super().__init__(episode)
        if episode.task == 'cube_restack':
            self.placed = ['green cube', 'red cube']

    def capture(self):
        state = super().capture()
        state.update(control_step=self.ticks, captured_at=time.time(), evidence_ref=f'fixture-{self.ticks}',
                     objects={name: dict(status='unknown' if self.occluded else 'visible')
                              for name in ['red cube', 'green cube']},
                     support_evidence={name: dict(status='unknown' if self.occluded else 'available')
                                       for name in ['red cube', 'green cube']})
        return state


class RobotSystemTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.manager = Manager(self.temp.name, SystemFixture)
        state = self.manager.create('system', 'cube_stack', 0,
                                    dict(on=['red cube', 'green cube'], empty_gripper=True))
        self.e = self.manager.episodes[state['id']]
        until(lambda: self.e.snapshot and self.e.status == 'ready')

    def tearDown(self):
        self.e.close()
        self.temp.cleanup()

    def system(self):
        return self.e.observe()['system']

    def skill(self, kind, name, arguments, request=None, system=None):
        system = system or self.system()
        sid = self.e.execute_skill(request or name, self.e.revision, system['state_id'],
                                   f'{system["goal_version"]}:{kind}', dict(name=name, arguments=arguments))
        until(lambda: self.e.active is None)
        return self.e.segments[sid]

    def test_goal_skills_and_executor_share_the_same_authoritative_task(self):
        before = self.system()
        self.assertEqual(before['task']['goal'], self.e.goal)
        self.assertEqual(before['world']['facts']['holding:red cube']['status'], 'failed')
        self.assertEqual(next(n for n in before['task']['nodes'] if n['kind'] == 'acquire')['status'], 'ready')
        self.e.decision('acquire', self.e.revision)
        acquired = self.skill('acquire', 'acquire_object', dict(object='red cube'))
        self.assertEqual(acquired['stage_verification']['status'], 'verified')
        self.assertNotEqual(acquired['goal_verification']['status'], 'verified')
        after = self.system()
        self.assertEqual(after['world']['facts']['holding:red cube']['status'], 'verified')
        self.assertEqual(next(n for n in after['task']['nodes'] if n['kind'] == 'place')['status'], 'ready')
        self.e.decision('place', self.e.revision)
        placed = self.skill('place', 'place_object', dict(object='red cube', support='green cube'))
        self.assertEqual(placed['goal_verification']['status'], 'verified')
        final = self.system()
        self.assertEqual(final['task']['status'], 'verified')
        self.assertEqual(next(n for n in final['task']['nodes'] if n['kind'] == 'verify')['status'], 'satisfied')
        self.assertEqual(self.e.used['decisions'], 2)
        self.assertEqual(self.e.used['calls'], 2)
        self.assertEqual(self.e.backend.reset_count, 1)
        persisted = json.loads((self.e.directory/'episode.json').read_text(encoding='utf-8'))
        self.assertEqual(persisted['system']['task']['status'], 'verified')
        self.assertEqual(placed['task_binding']['node_id'], f'{final["goal_version"]}:place')

    def test_public_http_rejects_all_unbound_code_and_keeps_bound_idempotency(self):
        server = serve(self.manager, '127.0.0.1', 0, 'system-http-test-token')
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f'http://127.0.0.1:{server.server_port}/episodes/{self.e.id}/segments'
        def post(body):
            request = urllib.request.Request(url, json.dumps(body).encode(), headers={
                'Authorization': 'Bearer system-http-test-token', 'Content-Type': 'application/json'})
            return json.loads(urllib.request.urlopen(request, timeout=3).read())
        try:
            original = dict(request_id='http-program', revision=self.e.revision, subgoal='acquire', code='pick("red cube")')
            for extra in [{}, dict(task_ref=None), dict(task_ref={}), dict(task_ref=dict(state_id='x'))]:
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    post({**original, **extra})
                self.assertEqual(caught.exception.code, 409)
            self.assertEqual(self.e.used['calls'], 0)
            self.assertEqual(self.e.segments, {})
            state = self.system()
            bound = {**original, 'task_ref': dict(state_id=state['state_id'], task_node='1:acquire')}
            first = post(bound)
            until(lambda: self.e.active is None)
            self.assertEqual(post(bound), first)
            self.assertEqual(self.e.used['calls'], 1)
        finally:
            server.shutdown()
            server.server_close()

    def test_attempt_history_cannot_substitute_for_current_satisfaction(self):
        result = self.skill('acquire', 'acquire_object', dict(object='red cube'))
        self.assertEqual(result['task_outcome']['phase'], 'verified')
        self.e.backend.held = None
        state = self.system()
        node = next(n for n in state['task']['nodes'] if n['kind'] == 'acquire')
        self.assertEqual(node['status'], 'ready')
        self.assertFalse(node['lifecycle']['current_satisfied'])
        self.assertEqual(node['lifecycle']['attempt_count'], 1)
        self.assertEqual(node['lifecycle']['last_outcome']['phase'], 'verified')
        self.assertNotEqual(state['task']['execution']['phase'], 'verified')
        self.assertEqual(state['world']['facts']['holding:red cube']['status'], 'failed')

    def test_code_and_skill_attempts_share_failure_lifecycle_and_goal_fence(self):
        rejected = self.bound_code('acquire', 'undefined_function()')
        self.assertEqual(rejected['task_outcome']['phase'], 'program_rejected')
        self.assertEqual(self.system()['task']['execution']['next_action'], 'revise_program')
        self.assertEqual(self.e.used['calls'], 0)
        acquired = self.skill('acquire', 'acquire_object', dict(object='red cube'))
        node = next(n for n in self.system()['task']['nodes'] if n['kind'] == 'acquire')
        self.assertEqual(node['lifecycle']['attempt_count'], 2)
        self.assertEqual(node['lifecycle']['last_attempt_id'], acquired['id'])
        events = [ev for ev in self.e.events if ev['kind'] == 'task_attempt_finished']
        self.assertEqual([e['outcome']['phase'] for e in events], ['program_rejected', 'verified'])
        self.e.update_goal(dict(holding='green cube'))
        state = self.system()
        self.assertIsNone(state['task']['execution']['last_attempt_id'])
        self.assertTrue(all(n['lifecycle']['attempt_count'] == 0 for n in state['task']['nodes']))

    def test_unknown_node_effect_has_evidence_recovery_direction(self):
        def occlude():
            self.e.backend.occluded = True
            return dict(status='verified')
        original = self.e.backend.tools
        self.e.backend.tools = lambda segment: {**original(segment), 'occlude': occlude}
        result = self.bound_code('acquire', 'occlude()')
        self.assertEqual(result['execution'], 'completed')
        self.assertEqual(result['node_verification']['status'], 'unknown')
        self.assertEqual(result['task_outcome']['phase'], 'evidence_insufficient')
        state = self.system()
        self.assertEqual(state['task']['execution']['phase'], 'evidence_insufficient')
        self.assertFalse(state['task']['execution']['automatic_retry'])

    def test_ordinary_unconfirmed_hold_fences_facts_decisions_and_all_dispatch(self):
        before = self.system()
        with self.e.lock:
            self.e.hold_serial += 1
            try:
                pending = self.system()
                self.assertTrue(pending['executor']['hold_pending'])
                self.assertEqual(pending['task']['execution']['phase'], 'holding')
                self.assertEqual(pending['task']['execution']['ready_nodes'], [])
                self.assertTrue(all(f['status'] == 'unknown' for f in pending['world']['facts'].values()))
                self.assertEqual(self.e.verify()['status'], 'unknown')
                self.assertEqual(self.e.verify(dict(empty_gripper=True))['status'], 'unknown')
                with self.assertRaises(HoldPending):
                    self.e.decision('hold-decision', self.e.revision)
                with self.assertRaises(HoldPending):
                    self.e.execute('engineering-hold', self.e.revision, 'hold', 'pick("red cube")')
                with self.assertRaises(DependenciesChanged):
                    self.skill('acquire', 'acquire_object', dict(object='red cube'), system=before)
            finally:
                self.e.hold_serial = self.e.hold_ack
        self.assertEqual(self.e.used['calls'], 0)
        self.assertEqual(self.e.used['decisions'], 0)
        self.assertEqual(self.e.segments, {})
        self.assertFalse(self.system()['executor']['hold_pending'])

    def test_new_evidence_can_clear_blocked_direction_without_erasing_failure(self):
        original = self.e.backend.tools
        def occlude():
            self.e.backend.occluded = True
            return dict(status='verified')
        self.e.backend.tools = lambda segment: {**original(segment), 'occlude': occlude}
        result = self.bound_code('acquire', 'occlude()')
        self.assertEqual(self.system()['task']['execution']['phase'], 'evidence_insufficient')
        self.e.backend.occluded = False
        current = self.system()
        self.assertEqual(current['task']['execution']['phase'], 'ready')
        self.assertEqual(current['task']['execution']['last_outcome']['phase'], 'evidence_insufficient')
        self.assertEqual(result['node_verification']['status'], 'unknown')

    def test_assistance_is_runtime_owned_idempotent_and_only_new_goal_resumes(self):
        before = self.system()
        state = self.e.request_assistance('help-1', self.e.revision, 'model', '需要恢复观测')
        self.assertEqual(state['assistance']['status'], 'awaiting_user')
        self.assertTrue(state['assistance']['hold_confirmed'])
        self.assertEqual(state['system']['task']['execution']['phase'], 'awaiting_user')
        self.assertEqual(state['system']['task']['execution']['ready_nodes'], [])
        events, serial, used = len(self.e.events), self.e.hold_serial, self.e.used.copy()
        self.e.request_assistance('help-1', self.e.revision, 'model', '需要恢复观测')
        self.assertEqual((len(self.e.events), self.e.hold_serial), (events, serial))
        with self.assertRaises(ValueError):
            self.e.request_assistance('help-1', self.e.revision, 'model', '不同请求')
        with self.assertRaises(AssistancePending):
            self.skill('acquire', 'acquire_object', dict(object='red cube'), system=before)
        with self.assertRaises(AssistancePending):
            self.e.execute('legacy-help', self.e.revision, '', 'pick("red cube")')
        with self.assertRaises(AssistancePending):
            self.e.decision('decision-help', self.e.revision)
        for key in ['calls', 'decisions', 'motion_steps']:
            self.assertEqual(self.e.used[key], used[key])
        self.assertGreaterEqual(self.e.used['idle_steps'], used['idle_steps'])
        self.e.decision('interpret-new-input', self.e.revision, purpose='interpretation')
        self.assertEqual(self.e.used['decisions'], used['decisions']+1)
        self.assertIsNotNone(self.e.assistance)
        with self.assertRaises(ValueError):
            self.e.decision('interpret-new-input', self.e.revision)
        self.e.update_goal(dict(holding='red cube'))
        self.assertIsNone(self.e.assistance)
        self.assertEqual(self.system()['goal_version'], 2)
        result = self.skill('acquire', 'acquire_object', dict(object='red cube'))
        self.assertEqual(result['task_outcome']['phase'], 'verified')
        self.assertEqual(self.e.backend.reset_count, 1)

    def test_assistance_during_motion_fences_late_api_and_preserves_attempt(self):
        before = self.system()
        sid = self.e.execute('motion-help', self.e.revision, 'wait', 'wait_motion(200)\npick("red cube")',
                             task_ref=dict(state_id=before['state_id'], task_node='1:acquire'))
        until(lambda: self.e.used['calls'] == 1)
        state = self.e.request_assistance('mid-motion', self.e.revision, 'executive', '动作需要协助')
        until(lambda: self.e.active is None)
        self.assertTrue(state['assistance']['hold_confirmed'])
        self.assertIsNone(self.e.backend.held)
        self.assertEqual(self.e.used['calls'], 1)
        segment = self.e.segments[sid]
        self.assertEqual(segment['execution'], 'cancelled')
        self.assertEqual(segment['task_outcome']['phase'], 'cancelled')
        self.assertEqual(self.system()['task']['execution']['phase'], 'awaiting_user')

    def test_goal_change_during_assistance_hold_does_not_restore_old_pause(self):
        entered, release = threading.Event(), threading.Event()
        original = self.e._hold
        def delayed(*args, **kwargs):
            if threading.current_thread().name == 'assistance-request':
                entered.set()
                release.wait(2)
            return original(*args, **kwargs)
        self.e._hold = delayed
        responses = []
        worker = threading.Thread(name='assistance-request', target=lambda: responses.append(
            self.e.request_assistance('old-help', self.e.revision, 'model', '旧目标需要协助')))
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            self.e.update_goal(dict(holding='green cube'))
            current = self.system()
            sid = self.e.execute('new-goal-motion', self.e.revision, 'acquire green',
                'wait_motion(50)\npick("green cube")',
                task_ref=dict(state_id=current['state_id'], task_node='2:acquire'))
            until(lambda: self.e.used['calls'] == 1)
        finally:
            release.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertIsNone(self.e.assistance)
        self.assertIsNone(responses[0]['assistance'])
        self.assertEqual(self.system()['goal_version'], 2)
        until(lambda: self.e.active is None)
        self.assertEqual(self.e.segments[sid]['execution'], 'completed')
        self.assertEqual(self.e.backend.held, 'green cube')
        self.assertEqual(self.system()['task']['execution']['phase'], 'verified')

    def test_new_frame_alone_does_not_permanently_invalidate_a_decision(self):
        old = self.system()
        until(lambda: self.e.snapshot['control_step'] > old['world']['source']['control_step'])
        self.system()
        acquired = self.skill('acquire', 'acquire_object', dict(object='red cube'), system=old)
        self.assertEqual(acquired['execution'], 'completed')
        self.assertEqual(acquired['task_binding']['requested_state_id'], old['state_id'])

    def test_unknown_dependencies_reject_before_any_api_or_motion(self):
        before = self.system()
        self.e.backend.occluded = True
        until(lambda: self.e.observe()['system']['world']['facts']['empty_gripper']['status'] == 'unknown')
        calls = self.e.used['calls']
        with self.assertRaises(DependenciesChanged):
            self.skill('acquire', 'acquire_object', dict(object='red cube'), system=before)
        self.assertEqual(self.e.used['calls'], calls)
        self.assertEqual(self.e.segments, {})

    def test_goal_change_even_back_to_same_goal_invalidates_old_nodes(self):
        old, goal = self.system(), copy.deepcopy(self.e.goal)
        self.e.update_goal(dict(holding='green cube'))
        self.e.update_goal(goal)
        with self.assertRaises(ValueError):
            self.skill('acquire', 'acquire_object', dict(object='red cube'), system=old)
        self.assertEqual(self.e.used['calls'], 0)
        self.assertEqual(self.system()['goal_version'], 3)

    def test_wrong_object_node_or_skill_cannot_expand_the_task(self):
        for kind, name, args in [('acquire', 'acquire_object', dict(object='green cube')),
                                 ('acquire', 'place_object', dict(object='red cube', support='green cube'))]:
            with self.assertRaises(ValueError):
                self.skill(kind, name, args)
        self.assertEqual(self.e.used['calls'], 0)

    def test_idempotent_skill_does_not_recompile_or_repeat_after_state_changes(self):
        state = self.system()
        result = self.skill('acquire', 'acquire_object', dict(object='red cube'), 'same', state)
        calls = self.e.used['calls']
        repeated = self.skill('acquire', 'acquire_object', dict(object='red cube'), 'same', state)
        self.assertEqual(repeated['id'], result['id'])
        self.assertEqual(self.e.used['calls'], calls)
        with self.assertRaises(ValueError):
            self.skill('acquire', 'acquire_object', dict(object='green cube'), 'same', state)

    def test_stale_sensor_or_cached_projection_cannot_keep_verified_facts(self):
        state = self.system()
        future = state['world']['source']['captured_at']+2.
        with patch('agentic_cap.robot_system.time.time', return_value=future):
            cached = self.e.state()['system']
        self.assertFalse(cached['world']['fresh'])
        self.assertTrue(all(f['status'] == 'unknown' for f in cached['world']['facts'].values()))
        self.assertTrue(all(n['status'] == 'unknown' for n in cached['task']['nodes']))

    def test_zero_remaining_budget_blocks_admission_without_action_or_new_segment(self):
        self.e.limits['calls'] = self.e.used['calls']
        with self.assertRaises(DependenciesChanged):
            self.skill('acquire', 'acquire_object', dict(object='red cube'))
        self.assertEqual(self.e.used['calls'], 0)
        self.assertEqual(self.e.segments, {})
        self.assertEqual(self.system()['task']['status'], 'open')
        self.assertEqual(self.system()['task']['execution']['phase'], 'budget_exhausted')
        self.assertEqual(self.system()['task']['execution']['ready_nodes'], [])

    def test_restart_marks_cached_world_unknown_and_never_replays_skills(self):
        self.skill('acquire', 'acquire_object', dict(object='red cube'))
        self.e._persist()
        other = Manager(self.temp.name, SystemFixture)
        persisted = other.get(self.e.id)
        self.assertEqual(persisted['status'], 'interrupted')
        self.assertIsNone(persisted['active_segment'])
        self.assertEqual(persisted['system']['task']['status'], 'unknown')
        self.assertTrue(all(f['status'] == 'unknown' for f in persisted['system']['world']['facts'].values()))
        self.assertEqual(other.episodes, {})

    def test_skill_arguments_are_closed_and_cannot_include_program_or_pose(self):
        for value in [dict(name='shell', arguments={}),
                      dict(name='acquire_object', arguments=dict(object='red cube', code='import os')),
                      dict(name='acquire_object', arguments=dict(object='red cube'), code='pass')]:
            with self.assertRaises(ValueError):
                validate_skill(value)

    def test_skill_cancel_and_goal_change_fence_late_api_results(self):
        original = self.e.backend.tools
        for change_goal in [False, True]:
            entered, gate = threading.Event(), threading.Event()

            def tools(segment):
                registered = original(segment)

                def delayed_pick(name):
                    entered.set()
                    gate.wait(2)
                    return registered['pick'](name)
                return dict(registered, pick=delayed_pick)

            self.e.backend.tools = tools
            state = self.system()
            sid = self.e.execute_skill(f'wait-{change_goal}', self.e.revision, state['state_id'],
                                       f'{state["goal_version"]}:acquire',
                                       dict(name='acquire_object', arguments=dict(object='red cube')))
            self.assertTrue(entered.wait(2))
            active = self.system()
            self.assertEqual(active['executor']['mode'], 'skill')
            self.assertEqual(next(n for n in active['task']['nodes'] if n['kind'] == 'acquire')['status'], 'running')
            try:
                if change_goal:
                    self.e.update_goal(dict(holding='green cube'))
                else:
                    self.e.cancel()
            finally:
                gate.set()
            until(lambda: self.e.active is None)
            self.assertEqual(self.e.segments[sid]['execution'], 'cancelled')
            self.assertEqual(self.e.segments[sid]['task_outcome']['phase'], 'cancelled')
            self.assertIsNone(self.e.backend.held)

    def test_reverse_stack_blocks_acquisition_and_exposes_clearance_as_a_task(self):
        self.e.backend.placed = ['green cube', 'red cube']
        state = self.system()
        nodes = {node['kind']: node for node in state['task']['nodes']}
        self.assertEqual(nodes['clear_acquire']['status'], 'ready')
        self.assertEqual(nodes['clear']['status'], 'blocked')
        self.assertEqual(nodes['acquire']['status'], 'blocked')
        with self.assertRaises(DependenciesChanged):
            self.skill('acquire', 'acquire_object', dict(object='red cube'), system=state)
        self.assertEqual(self.e.used['calls'], 0)

    def bound_code(self, kind, code, request='code', state=None, contract=None):
        state = state or self.system()
        ref = dict(state_id=state['state_id'], task_node=f'{state["goal_version"]}:{kind}')
        sid = self.e.execute(request, self.e.revision, kind, code, contract, task_ref=ref)
        until(lambda: self.e.active is None)
        return self.e.segments[sid]

    def test_restack_blocker_skill_and_cap_clearing_share_authoritative_task_nodes(self):
        self.e.backend.placed = ['green cube', 'red cube']
        acquired = self.skill('clear_acquire', 'acquire_object', dict(object='green cube'))
        self.assertEqual(acquired['node_verification']['status'], 'verified')
        self.assertEqual(acquired['stage_verification']['condition'], dict(holding='green cube'))
        cleared = self.bound_code('clear', 'place_on("table")')
        self.assertNotIn('skill', cleared)
        self.assertEqual(cleared['task_binding']['node_id'], '1:clear')
        self.assertEqual(cleared['node_verification']['status'], 'verified')
        self.assertNotEqual(cleared['goal_verification']['status'], 'verified')
        state = self.system()
        self.assertEqual(next(n for n in state['task']['nodes'] if n['kind'] == 'acquire')['status'], 'ready')
        self.skill('acquire', 'acquire_object', dict(object='red cube'), request='target')
        self.skill('place', 'place_object', dict(object='red cube', support='green cube'))
        self.assertEqual(self.e.finish(self.e.revision)['status'], 'verified')
        self.assertEqual(self.e.backend.reset_count, 1)
        self.assertEqual(self.e.used['calls'], 4)
        self.assertTrue(all(s.get('task_binding') and s.get('node_verification') for s in self.e.segments.values()))

    def test_code_stage_contract_cannot_redefine_runtime_node_completion(self):
        self.e.backend.placed = ['green cube', 'red cube']
        self.skill('clear_acquire', 'acquire_object', dict(object='green cube'))
        result = self.bound_code('clear', 'wait_motion(1)', contract=dict(postcondition=dict(holding='green cube')))
        self.assertEqual(result['execution'], 'completed')
        self.assertEqual(result['stage_verification']['status'], 'verified')
        self.assertEqual(result['node_verification']['status'], 'failed')
        self.assertIsNotNone(result.get('feedback'))
        self.assertNotEqual(result['goal_verification']['status'], 'verified')

    def test_bound_code_idempotency_and_changed_dependencies_do_not_repeat_api_calls(self):
        before = self.system()
        result = self.bound_code('acquire', 'pick("red cube")', state=before)
        repeated = self.bound_code('acquire', 'pick("red cube")', state=before)
        self.assertEqual(result['id'], repeated['id'])
        self.assertEqual(self.e.used['calls'], 1)
        with self.assertRaises(ValueError):
            self.bound_code('acquire', 'pick("green cube")', state=before)
        with self.assertRaises(DependenciesChanged):
            self.bound_code('acquire', 'pick("red cube")', request='other', state=before)
        self.assertEqual(self.e.used['calls'], 1)

    def test_unknown_or_superseded_task_reference_rejects_code_before_api(self):
        state = self.system()
        self.e.backend.occluded = True
        until(lambda: self.system()['world']['facts']['empty_gripper']['status'] == 'unknown')
        with self.assertRaises(DependenciesChanged):
            self.bound_code('acquire', 'pick("red cube")', state=state)
        self.e.update_goal(dict(holding='green cube'))
        with self.assertRaises(ValueError):
            self.bound_code('acquire', 'pick("red cube")', state=state)
        self.assertEqual(self.e.used['calls'], 0)

    def test_monitor_contradiction_cannot_disagree_with_final_goal_verification(self):
        self.e.update_goal(dict(holding='red cube'))
        self.e.backend.held = 'red cube'
        self.assertEqual(self.e.verify()['status'], 'verified')
        with self.e.lock:
            self.e.interrupt_public_hold(dict(status='violated', object_name='red cube',
                                             arm=dict(control_step=10)))
            system = self.system()
            self.assertEqual(system['world']['facts']['goal']['status'], 'unknown')
            self.assertEqual(self.e.finish(self.e.revision)['status'], 'unknown')
            self.assertFalse(any(node['status'] == 'ready' for node in system['task']['nodes'] if node['skills']))
        until(lambda: self.e.runtime_alert['hold_confirmed'])
        system = self.system()
        self.assertEqual(system['world']['facts']['holding:red cube']['status'], 'failed')
        self.assertEqual(system['world']['facts']['goal']['status'], self.e.finish(self.e.revision)['status'])
        self.assertEqual(self.e.verify(dict(holding='red cube'))['status'], 'failed')
        self.assertEqual(system['world']['facts']['holding_geometry:red cube']['status'], 'unknown')


if __name__ == '__main__':
    unittest.main()
