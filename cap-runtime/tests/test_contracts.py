"""Protocol/fault tests. Fixture outcomes never count as robot performance."""
import unittest
import test_runtime as fixtures
from agentic_cap.feedback import MotionError

until = fixtures.until


class ContractTests(unittest.TestCase):
    setUp = fixtures.RuntimeTests.setUp
    tearDown = fixtures.RuntimeTests.tearDown

    def segment(self, key, code, contract=None):
        sid = self.e.execute(key, self.e.revision, 'phase', code, contract)
        until(lambda: self.e.active is None)
        return self.e.segments[sid]

    def test_stage_success_does_not_claim_final_success(self):
        self.e.update_goal({'on': ['red cube', 'green cube'], 'empty_gripper': True})
        result = self.segment('lift', 'pick("red cube")', {'postcondition': {'holding': 'red cube'}})
        self.assertEqual(result['execution'], 'completed')
        self.assertEqual(result['stage_verification']['status'], 'verified')
        self.assertEqual(result['goal_verification']['status'], 'failed')
        self.assertEqual(result['effect'], 'failed')
        placed = self.segment('place', 'place_on("green cube")', {
            'preconditions': [{'holding': 'red cube'}], 'postcondition': self.e.goal})
        self.assertEqual(placed['stage_verification']['status'], 'verified')
        self.assertEqual(placed['effect'], 'verified')
        self.assertEqual(self.e.backend.reset_count, 1)

    def test_failed_and_unknown_preconditions_dispatch_nothing(self):
        for key, occluded in [('failed', False), ('unknown', True)]:
            self.e.backend.occluded = occluded
            before = self.e.used['calls']
            result = self.segment(key, 'pick("red cube")', {'preconditions': [{'holding': 'green cube'}]})
            self.assertEqual(self.e.used['calls'], before)
            self.assertEqual(result['execution'], 'failed')
            self.assertEqual(result['feedback']['phase'], 'precondition')
            self.assertEqual(result['feedback']['category'], 'insufficient_evidence' if occluded else 'condition_not_satisfied')
            self.assertIsNone(self.e.backend.held)

    def test_api_unknown_preserves_evidence_and_call_identity(self):
        original = self.e.backend.tools
        def tools(segment):
            return {**original(segment), 'check': lambda: dict(status='unknown', evidence={'reason': 'object_not_visible'})}
        self.e.backend.tools = tools
        result = self.segment('unknown', 'check()\npick("red cube")')
        self.assertEqual(result['feedback']['category'], 'insufficient_evidence')
        self.assertEqual(result['feedback']['evidence']['evidence']['reason'], 'object_not_visible')
        self.assertEqual(self.e.used['calls'], 1)
        events = [e for e in self.e.events if e['kind'] in {'api_started', 'api_result', 'api_error'}]
        self.assertEqual(len({e['call_id'] for e in events}), 1)
        self.assertEqual(result['feedback']['call_id'], events[0]['call_id'])
        self.assertEqual(result['feedback']['causal_status'], 'not_established')

    def test_motion_failure_keeps_encoder_evidence(self):
        def tools(_segment):
            def motion():
                raise MotionError(dict(status='failed', joint_error_rad=.3, control_steps=20))
            return dict(motion=motion)
        self.e.backend.tools = tools
        result = self.segment('motion', 'motion()')
        self.assertEqual(result['feedback']['category'], 'motion_target_not_reached')
        self.assertEqual(result['feedback']['evidence']['joint_error_rad'], .3)

    def test_field_error_then_repair_retains_scene_budget_and_independent_stage_verification(self):
        self.e.update_goal({'on': ['red cube', 'green cube'], 'empty_gripper': True})
        identity = self.e.id
        self.e.decision('grasp', self.e.revision)
        grasp = self.segment('grasp', 'pick("red cube")')
        self.assertEqual(grasp['execution'], 'completed')
        self.assertEqual(grasp['stage_verification']['reason'], 'No public postcondition declared')
        before_calls = self.e.used['calls']
        self.e.decision('bad-field', self.e.revision)
        bad = self.segment('bad-field', 'state=get_execution_state()\np=state[0]\nplace_on("green cube")')
        self.assertEqual(bad['execution'], 'failed')
        self.assertEqual(bad['feedback']['category'], 'invalid_program')
        self.assertEqual(bad['feedback']['evidence']['line'], 2)
        self.assertIn('joint_positions', bad['feedback']['evidence']['available_fields'])
        self.assertEqual(self.e.used['calls']-before_calls, 1)
        self.assertEqual(self.e.backend.held, 'red cube')
        self.assertIsNone(self.e.backend.placed)
        self.e.decision('fixed-field', self.e.revision)
        fixed = self.segment('fixed-field', 'state=get_execution_state()\np=state["joint_positions"]\nwait_motion(2)',
                             {'postcondition': {'holding': 'red cube'}})
        self.assertEqual(fixed['execution'], 'completed')
        self.assertEqual(fixed['stage_verification']['status'], 'verified')
        self.assertEqual(fixed['goal_verification']['status'], 'failed')
        self.assertEqual(self.e.id, identity)
        self.assertEqual(self.e.used['decisions'], 3)
        self.assertEqual(self.e.used['calls'], 4)

    def test_verifier_error_is_unknown_and_releases_segment(self):
        def broken(*_args):
            raise RuntimeError('injected sensor exception')
        self.e.backend.verify = broken
        result = self.segment('broken', 'pick("red cube")', {'postcondition': {'holding': 'red cube'}})
        self.assertEqual(result['execution'], 'completed')
        self.assertEqual(result['effect'], 'unknown')
        self.assertEqual(result['stage_verification']['status'], 'unknown')
        self.assertIsNone(self.e.active)
        self.assertEqual(self.e.finish(self.e.revision)['status'], 'unknown')

    def test_contract_is_part_of_idempotency_and_validated_before_execution(self):
        contract = {'postcondition': {'holding': 'red cube'}}
        sid = self.e.execute('idempotent-contract', 1, 'phase', 'wait_motion()', contract)
        self.assertEqual(sid, self.e.execute('idempotent-contract', 1, 'phase', 'wait_motion()', contract))
        with self.assertRaises(ValueError):
            self.e.execute('idempotent-contract', 1, 'phase', 'wait_motion()', {'postcondition': {'holding': 'green cube'}})
        until(lambda: self.e.active is None)
        for invalid in [{'postcondition': {'sim_truth': True}}, {'preconditions': [self.e.goal]*5}, {'postcondition': {}}, {'postcondition': {'holding': 'table'}}]:
            with self.subTest(contract=invalid), self.assertRaises(ValueError):
                self.e.execute('invalid', 1, 'phase', 'pick("red cube")', invalid)

    def test_cancelled_postcondition_is_not_evaluated_as_success(self):
        sid = self.e.execute('cancel-stage', 1, 'phase', 'wait_motion(200)', {'postcondition': {'holding': 'red cube'}})
        until(lambda: self.e.motion)
        self.e.cancel()
        until(lambda: self.e.active is None)
        result = self.e.segments[sid]
        self.assertEqual(result['feedback']['category'], 'cancelled')
        self.assertEqual(result['stage_verification']['status'], 'unknown')
