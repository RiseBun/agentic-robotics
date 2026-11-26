"""Conditional path costs and authority; no physical success from fixture tests."""
import copy
import unittest
from unittest.mock import patch
from agentic_cap.continuation_resources import completion_paths
import test_reorientation as rotation


def cost(low,high=None,required=None):
    high=low if high is None else high
    return dict(minimum_calls=low,maximum_calls=high,required_calls=high if required is None else required,status='fits')


def node(identity,next_node=None,estimate=None,status='ready',terminal=False):
    return dict(id=identity,kind=identity.split(':')[-1],status=status,requirements=[],
        resources=dict(skills={'skill-'+identity:estimate} if estimate is not None else {}),
        continuation=dict(goal_if_public_postcondition_verified=True) if terminal else dict(next_node=next_node))


class CompletionPathTests(unittest.TestCase):
    def path(self,nodes,calls):
        return completion_paths(nodes,[nodes[0]['id']],dict(calls=calls))[0]

    def test_each_stage_fits_but_three_plus_six_cannot_finish_with_eight(self):
        nodes=[node('1:reorient','1:place',cost(3)),node('1:place',estimate=cost(6),terminal=True,status='blocked')]
        result=self.path(nodes,8)
        self.assertEqual(result['minimum_calls'],9)
        self.assertEqual(result['required_calls'],9)
        self.assertEqual(result['status'],'insufficient_registered_path')
        self.assertEqual(result['steps'][1]['execution'],'deferred_not_authorized')
        self.assertEqual(result['steps'][1]['observed_status'],'blocked')
        self.assertEqual(result['future_model_requests_if_one_stage_per_reply'],1)
        self.assertEqual(nodes[1]['status'],'blocked')

    def test_nine_calls_only_makes_the_path_conditional_not_verified(self):
        result=self.path([node('r','p',cost(3)),node('p',estimate=cost(6),terminal=True)],9)
        self.assertEqual(result['status'],'conditional')
        self.assertNotIn(result['status'],['verified','fits'])
        self.assertEqual(len(result['assumptions']),4)

    def test_unknown_future_code_cost_is_not_assumed_zero(self):
        result=self.path([node('a','clear',cost(6)),node('clear','p'),node('p',estimate=cost(6),terminal=True)],40)
        self.assertEqual(result['status'],'unknown')
        self.assertIsNone(result['minimum_calls'])
        self.assertIsNone(result['required_calls'])
        self.assertTrue(result['reaches_goal_if_all_declared_effects_verify'])

    def test_optional_inspection_branch_uses_a_range_and_headroom(self):
        result=self.path([node('inspect','p',cost(1,6)),node('p',estimate=cost(6),terminal=True)],10)
        self.assertEqual(result['minimum_calls'],7)
        self.assertEqual(result['maximum_calls'],12)
        self.assertEqual(result['status'],'conditional_branch_budget')
        result=self.path([node('release',estimate=cost(1,1,3),terminal=True)],3)
        self.assertEqual(result['required_calls'],3)

    def test_current_satisfied_stage_can_be_skipped_only_with_explicit_assumption(self):
        result=self.path([node('a','support',cost(6)),node('support','p',cost(1,6),status='satisfied'),node('p',estimate=cost(6),terminal=True)],12)
        self.assertEqual(result['minimum_calls'],12)
        self.assertEqual(len(result['skipped_currently_satisfied']),1)
        self.assertIn('persist',result['skipped_currently_satisfied'][0]['assumption'])

    def test_broken_graph_and_invalid_costs_stay_unknown(self):
        for nodes in [[node('a','a',cost(3))],[node('a','missing',cost(3))],
                      [node('a',estimate=cost(-1),terminal=True)],
                      [node('a',estimate=cost(True),terminal=True)]]:
            self.assertEqual(self.path(nodes,10)['status'],'unknown')

    def test_nonready_nodes_do_not_gain_a_candidate_from_this_projection(self):
        nodes=[node('a',estimate=cost(3),terminal=True)]
        self.assertEqual(completion_paths(nodes,[],dict(calls=40)),[])


class RuntimeContinuationTests(unittest.TestCase):
    setUp=rotation.ReorientationTests.setUp
    tearDown=rotation.ReorientationTests.tearDown
    hold=rotation.ReorientationTests.hold
    prepare=rotation.ReorientationTests.prepare

    def test_runtime_uses_same_compiler_profiles_without_authorizing_future_placement(self):
        self.prepare();self.e.used['calls']=32
        state=self.e.observe()['system']
        path=next(p for p in state['resources']['completion_paths'] if p['start_node']=='1:reorient')
        self.assertEqual(path['minimum_calls'],9)
        self.assertEqual(path['status'],'insufficient_registered_path')
        self.assertEqual(next(n for n in state['task']['nodes'] if n['kind']=='reorient')['resources']['skills']['reorient_held_object']['status'],'fits')
        self.assertEqual(next(n for n in state['task']['nodes'] if n['kind']=='place')['status'],'blocked')
        self.assertEqual(self.e.used['calls'],32)
        self.assertEqual(self.e.backend.moves,[])
        self.e.used['calls']=31
        path=next(p for p in self.e.observe()['system']['resources']['completion_paths'] if p['start_node']=='1:reorient')
        self.assertEqual(path['status'],'conditional')
        self.assertEqual(self.e.backend.reset_count,1)

    def test_cached_paths_disappear_on_stale_budget_hold_and_new_goal(self):
        self.prepare();current=self.e.observe()['system']
        self.assertTrue(current['resources']['completion_paths'])
        self.e.used['calls']+=1
        self.assertEqual(self.e.state()['system']['resources']['completion_paths'],[])
        self.e.observe()
        with patch('agentic_cap.robot_system.time.time',return_value=current['world']['source']['captured_at']+2):
            self.assertEqual(self.e.state()['system']['resources']['completion_paths'],[])
        with self.e.lock:
            self.e.hold_serial+=1
            self.assertEqual(self.e.state()['system']['resources']['completion_paths'],[])
        self.e.update_goal(dict(holding='green cube'))
        new=self.e.observe()['system']
        self.assertEqual(new['goal_version'],2)
        self.assertFalse(any(p['start_node'].startswith('1:') for p in new['resources']['completion_paths']))


class RestorePriorityTests(unittest.TestCase):
    setUp=rotation.ReorientationTests.setUp
    tearDown=rotation.ReorientationTests.tearDown
    hold=rotation.ReorientationTests.hold

    def prepare_low(self):
        import test_registered_skills as registered
        registered.RegisteredSkillTests.prepare_retreat(self)
        original=self.e.backend.capture
        def capture():
            state=original();state['capabilities']=['inspect_support','reorient_held_object']
            state['support_evidence']['green cube']=dict(status='unknown')
            return state
        self.e.backend.capture=capture
        from test_runtime import until
        until(lambda:'inspect_support' in self.e.observe().get('capabilities',[]))

    def test_recorded_retreat_is_prioritized_without_a_complete_goal_budget(self):
        self.prepare_low();self.e.used['calls']=38
        system=self.e.observe()['system']
        priority=system['task']['execution']['priority_restore_routes']
        self.assertEqual([p['skill'] for p in priority],['retreat_from_descent'])
        self.assertEqual(priority[0]['resources']['required_calls'],2)
        path=next(p for p in system['resources']['completion_paths'] if p['start_node']=='1:recover')
        self.assertTrue(path['completion_budget_must_not_suppress_current_action'])
        self.assertEqual(path['current_action_role'],'restore_recorded_pose')
        self.assertEqual(self.e.backend.moves,[])

    def test_low_inspection_node_is_blocked_until_recorded_descent_is_reversed(self):
        self.prepare_low();self.e.backend.low_holding_unknown=False
        system=self.e.observe()['system']
        self.assertEqual(system['world']['facts']['holding:red cube']['status'],'verified')
        support=next(n for n in system['task']['nodes'] if n['kind']=='support')
        self.assertEqual(support['status'],'blocked')
        requirement=next(r for r in support['requirements'] if r['fact']=='retreat_path:red cube')
        self.assertEqual(requirement['expected'],'unknown')
        self.assertEqual(requirement['actual'],'verified')
        from agentic_cap.core import DependenciesChanged
        with self.assertRaises(DependenciesChanged):
            self.e.execute_skill('low-inspect',self.e.revision,system['state_id'],'1:support',
                dict(name='inspect_support',arguments=dict(object='red cube',support='green cube')))
        self.assertEqual(self.e.used['calls'],0)
        self.assertEqual(self.e.backend.moves,[])

    def test_unaffordable_retreat_is_not_presented_as_executable_priority(self):
        self.prepare_low();self.e.used['calls']=39
        self.assertEqual(self.e.observe()['system']['task']['execution']['priority_restore_routes'],[])
