import copy
import threading
import time
import unittest
from types import SimpleNamespace
import test_registered_skills as base
from agentic_cap.skills import compile_skill
from agentic_cap.active_evidence import install_inspector
from agentic_cap.core import Halt
from test_runtime import until


class RecoveryCostTests(unittest.TestCase):
    setUp=base.RegisteredSkillTests.setUp
    tearDown=base.RegisteredSkillTests.tearDown
    skill=base.RegisteredSkillTests.skill
    hold=base.RegisteredSkillTests.hold

    def test_verified_open_gripper_skips_only_redundant_open_and_preserves_full_grasp(self):
        system=self.e.observe()['system'];snapshot=self.e.observe()
        code,_=compile_skill(dict(name='acquire_object',arguments=dict(object='red cube')),system,snapshot)
        self.assertNotIn('open_gripper()',code)
        result=self.skill('acquire','acquire_object',dict(object='red cube'))
        self.assertEqual(result['execution'],'completed',result.get('error'))
        self.assertEqual(result['node_verification']['status'],'verified')
        self.assertEqual([x['name'] for x in result['api_attempts']],['get_object_pose','goto_pose','close_gripper','get_execution_state','goto_pose','check_holding'])
        self.assertEqual(self.e.used['calls'],6)

    def test_missing_stale_or_closed_aperture_keeps_open_command_in_program(self):
        snapshot=self.e.observe();system=snapshot['system']
        for change in [lambda s:s.pop('gripper_joint_positions'),lambda s:s.update(captured_at=time.time()-2),
                       lambda s:s.update(last_gripper_command='closed'),lambda s:s.update(gripper_joint_positions=[.01,-.01])]:
            current=copy.deepcopy(snapshot);change(current)
            code,_=compile_skill(dict(name='acquire_object',arguments=dict(object='red cube')),system,current)
            self.assertIn('open_gripper()',code)


class ReleaseBudgetTests(unittest.TestCase):
    def tools(self,remaining):
        snapshot=dict(captured_at=time.time(),control_step=100,evidence_ref='frame-000100.npz',
            objects={'green cube':dict(status='visible',surface_median_xyz=[.5,0,.04],surface_span_xyz=[.04,.04,.04])})
        events=[];opened=[]
        def event(kind,**values):
            row=dict(id=len(events)+1,kind=kind,**values);events.append(row);return row
        episode=SimpleNamespace(goal=dict(on=['red cube','green cube'],empty_gripper=True),gripper=0,
            used={'calls':40-remaining},limits={'calls':40},checkpoint=lambda _:None,
            observe=lambda:snapshot,event=event,events=events,lock=threading.RLock())
        backend=SimpleNamespace(episode=episode,held_hint='red cube',holding_results={'red cube':'verified'},reference=None,tick=100)
        segment=dict(id='place',revision=1,skill=dict(name='place_object'))
        tools=install_inspector(backend,segment,{'open_gripper':lambda:opened.append(True)})
        return tools,opened,events

    def test_registered_release_denied_before_open_without_two_remaining_calls(self):
        for remaining in [0,1]:
            tools,opened,events=self.tools(remaining)
            with self.assertRaises(Halt):tools['open_gripper']()
            self.assertEqual(opened,[])
            self.assertTrue(any(e['kind']=='placement_release_budget_blocked' for e in events))

    def test_exact_two_call_headroom_allows_release_under_unchanged_evidence_guard(self):
        tools,opened,events=self.tools(2)
        tools['open_gripper']()
        self.assertEqual(opened,[True])
        self.assertFalse(any(e['kind']=='placement_release_budget_blocked' for e in events))


    def test_generated_code_release_uses_the_same_followup_budget_guard(self):
        tools,opened,events=self.tools(1)
        # No registered skill marker: this is a generated CaP release.
        # Rebuild the wrapper with the same public state and budget.
        from types import SimpleNamespace
        snapshot=dict(captured_at=time.time(),control_step=100,evidence_ref='frame',
            objects={'green cube':dict(status='visible',surface_median_xyz=[.5,0,.04],surface_span_xyz=[.04,.04,.04])})
        episode=SimpleNamespace(goal=dict(on=['red cube','green cube'],empty_gripper=True),gripper=0,
            used={'calls':39},limits={'calls':40},checkpoint=lambda _:None,observe=lambda:snapshot,
            event=lambda kind,**values:dict(id=1,kind=kind,**values),events=[],lock=threading.RLock())
        backend=SimpleNamespace(episode=episode,held_hint='red cube',holding_results={'red cube':'verified'},reference=None,tick=100)
        opened=[]
        tools=install_inspector(backend,dict(id='cap',revision=1),{'open_gripper':lambda:opened.append(True)})
        with self.assertRaises(Halt):tools['open_gripper']()
        self.assertEqual(opened,[])
