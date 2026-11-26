"""Stage API preflight; synthetic sensors do not establish robot performance."""
import ast
import unittest
from agentic_cap.interpreter import Interpreter
from agentic_cap.program_resources import program_calls, assess_calls
import test_registered_skills as registered
from test_runtime import until


class ProgramResourceTests(unittest.TestCase):
    def estimate(self,code):
        names={'move','sense','inspect_support','inspect_placement'}
        tree=Interpreter({name:None for name in names},lambda:None).validate(code)
        return program_calls(tree,names)

    def test_calls_inside_arguments_branches_loops_and_assignment_indices(self):
        code='x=[0,0]\nx[sense()]=sense()\nif sense():\n    for n in [1,2]:\n        move()\nelse:\n    move()'
        result=self.estimate(code)
        self.assertEqual((result['minimum_calls'],result['maximum_calls']),(4,5))
        for branch in [False,True]:
            calls=[]
            def sense():calls.append('sense');return int(branch)
            def move():calls.append('move')
            Interpreter(dict(sense=sense,move=move),lambda:None).run(code)
            self.assertTrue(result['minimum_calls']<=len(calls)<=result['maximum_calls'])

    def test_dynamic_loop_uses_the_interpreters_bound_and_counts_else(self):
        result=self.estimate('xs=[1,2]\nfor n in xs:\n    move()\nelse:\n    sense()')
        self.assertEqual((result['minimum_calls'],result['maximum_calls']),(1,17))
        result=self.estimate('for n in range(100):\n    move()')
        self.assertEqual(result['maximum_calls'],16)

    def test_builtins_are_free_and_inspector_child_calls_are_included(self):
        result=self.estimate('x=max([1,2])\ninspect_support("green cube","red cube")\ninspect_placement("red cube","green cube")')
        self.assertEqual((result['minimum_calls'],result['maximum_calls']),(2,10))
        self.assertEqual(assess_calls(result,9)['status'],'insufficient')
        self.assertEqual(assess_calls(result,10)['status'],'fits')
        self.assertIsNone(result['motion_steps_required'])
        self.assertIsNone(result['wall_seconds_required'])

    def test_constant_branch_and_short_circuit_never_undercount_path(self):
        result=self.estimate('if False:\n    move()\nx=sense() and move()')
        self.assertEqual((result['minimum_calls'],result['maximum_calls']),(1,2))


class StageAdmissionTests(unittest.TestCase):
    setUp=registered.RegisteredSkillTests.setUp
    tearDown=registered.RegisteredSkillTests.tearDown
    skill=registered.RegisteredSkillTests.skill
    hold=registered.RegisteredSkillTests.hold

    def test_place_with_five_calls_left_rejects_before_descent_or_release(self):
        self.hold();self.e.used['calls']=35
        before=list(self.e.backend.moves)
        system=self.e.observe()['system']
        node=next(n for n in system['task']['nodes'] if n['kind']=='place')
        estimate=node['resources']['skills']['place_object']
        self.assertEqual(estimate['maximum_calls'],6)
        self.assertEqual(estimate['status'],'insufficient')
        self.assertTrue(any(route['skill']=='place_object' for route in system['task']['execution']['resource_blocked_skill_routes']))
        self.assertFalse(any(route['skill']=='place_object' for route in system['task']['execution']['affordable_skill_routes']))
        result=self.skill('place','place_object',dict(object='red cube',support='green cube'))
        self.assertEqual(result['execution'],'failed')
        self.assertEqual(result['feedback']['category'],'resources_insufficient')
        self.assertEqual(result['task_outcome']['phase'],'resources_insufficient')
        self.assertEqual(result['api_attempts'],[])
        self.assertEqual(self.e.backend.moves,before)
        self.assertEqual(self.e.backend.held,'red cube')
        self.assertEqual(self.e.used['calls'],35)
        repeat=self.e.execute_skill('place_object',result['revision'],result['task_binding']['requested_state_id'],'1:place',dict(name='place_object',arguments=dict(object='red cube',support='green cube')))
        self.assertEqual(repeat,result['id'])
        self.assertEqual(len(self.e.segments),1)

    def test_exact_six_call_place_completes_full_program_and_postcondition(self):
        self.hold();self.e.used['calls']=34
        result=self.skill('place','place_object',dict(object='red cube',support='green cube'))
        self.assertEqual(result['execution'],'completed',result.get('error'))
        self.assertEqual(result['node_verification']['status'],'verified')
        self.assertEqual(len(result['api_attempts']),6)
        self.assertEqual(result['resources']['maximum_calls'],6)
        self.assertEqual(self.e.used['calls'],40)
        self.assertIsNone(self.e.backend.held)

    def test_bound_cap_program_uses_its_actual_cost_before_first_api(self):
        system=self.e.observe()['system'];self.e.used['calls']=39
        sid=self.e.execute('cap',self.e.revision,'bounded program','get_execution_state()\nget_execution_state()',
                           task_ref=dict(state_id=system['state_id'],task_node='1:acquire'))
        until(lambda:self.e.active is None)
        result=self.e.segments[sid]
        self.assertEqual(result['feedback']['category'],'resources_insufficient')
        self.assertEqual(result['api_attempts'],[])
        self.assertEqual(self.e.used['calls'],39)
        self.assertEqual(self.e.backend.moves,[])

    def test_program_error_is_not_misclassified_as_resource_shortage(self):
        self.e.used['calls']=40
        sid=self.e.execute('typo',self.e.revision,'invalid','p=missing\nget_execution_state()')
        until(lambda:self.e.active is None)
        self.assertEqual(self.e.segments[sid]['feedback']['category'],'invalid_program')
        self.assertEqual(self.e.segments[sid]['api_attempts'],[])


class ReleaseHeadroomTests(unittest.TestCase):
    def estimate(self,code):
        tools={'open_gripper','get_execution_state','goto_pose'}
        tree=Interpreter({name:None for name in tools},lambda:None).validate(code)
        return program_calls(tree,tools,final_placement=True)

    def test_single_release_cannot_hide_required_followup_by_splitting_code(self):
        result=self.estimate('open_gripper()')
        self.assertEqual(result['maximum_calls'],1)
        self.assertEqual(result['required_calls'],3)
        self.assertEqual(assess_calls(result,2)['status'],'insufficient')
        self.assertEqual(assess_calls(result,3)['status'],'fits')

    def test_complete_release_tail_is_not_double_counted(self):
        result=self.estimate('open_gripper()\nget_execution_state()\ngoto_pose([0,0,0],[1,0,0,0])')
        self.assertEqual(result['maximum_calls'],3)
        self.assertEqual(result['required_calls'],3)

    def test_branch_and_loop_releases_preserve_headroom_on_every_bounded_path(self):
        result=self.estimate('for n in [1,2]:\n    open_gripper()')
        self.assertEqual(result['maximum_calls'],2)
        self.assertEqual(result['required_calls'],4)
        result=self.estimate('s=get_execution_state()\nif s:\n    open_gripper()\nelse:\n    goto_pose([0,0,0],[1,0,0,0])')
        self.assertEqual(result['required_calls'],4)
