import copy
import math
import unittest
import threading
import time
from agentic_cap.skills import rotation_clearance, compile_skill
from agentic_cap.placement_retry import placement_context
import test_registered_skills as base
from test_runtime import until


class ReorientationTests(unittest.TestCase):
    setUp=base.RegisteredSkillTests.setUp
    tearDown=base.RegisteredSkillTests.tearDown
    skill=base.RegisteredSkillTests.skill
    hold=base.RegisteredSkillTests.hold
    def prepare(self):
        original=self.e.backend.capture
        def capture():
            state=original();state['capabilities']=['reorient_held_object'];return state
        self.e.backend.capture=capture
        self.hold()
        until(lambda:'reorient_held_object' in self.e.observe().get('capabilities',[]))
        state=self.e.observe();system=state['system']
        context=placement_context(state,system['world']['facts'],dict(object='red cube',support='green cube'))
        failed=dict(id='blocked-release',execution='failed',effect='unknown',
            task_binding=dict(node_id='1:place',goal_version=1,retry_context=context),
            feedback=dict(category='insufficient_evidence',api='open_gripper',evidence=dict(evidence=dict(
                status='unknown',object_name='green cube',reason='support_horizontal_extent_truncated'))),
            node_verification=dict(status='unknown'))
        with self.e.lock:self.e.segments[failed['id']]=failed
        return failed

    def test_actual_restricted_program_changes_only_yaw_and_keeps_grasp_and_old_failure(self):
        failed=self.prepare();before=self.e.backend.xyz.copy();prior=self.e.backend.quat.copy()
        system=self.e.observe()['system'];node=next(n for n in system['task']['nodes'] if n['kind']=='reorient')
        self.assertEqual(node['status'],'ready')
        result=self.skill('reorient','reorient_held_object',dict(object='red cube'))
        self.assertEqual(result['execution'],'completed',result.get('error'))
        self.assertEqual(result['node_verification']['status'],'verified')
        self.assertEqual([x['name'] for x in result['api_attempts']],['get_execution_state','goto_pose','check_holding'])
        self.assertEqual(self.e.backend.xyz,before)
        dot=abs(sum(a*b for a,b in zip(prior,self.e.backend.quat)))
        self.assertAlmostEqual(math.degrees(2*math.acos(dot)),25,places=4)
        self.assertEqual(self.e.backend.held,'red cube')
        self.assertEqual(self.e.segments[failed['id']]['execution'],'failed')
        after=self.e.observe()['system']
        self.assertEqual(next(n for n in after['task']['nodes'] if n['kind']=='place')['status'],'ready')
        self.assertNotEqual(after['world']['facts']['goal']['status'],'verified')
        self.assertEqual(self.e.backend.reset_count,1)

    def test_bound_code_cannot_use_reorientation_node_to_release_or_bypass_trial_limit(self):
        self.prepare();system=self.e.observe()['system'];calls=self.e.used['calls'];count=len(self.e.segments)
        with self.assertRaisesRegex(ValueError,'registered skill only'):
            self.e.execute('injected',self.e.revision,'release','open_gripper()',task_ref=dict(state_id=system['state_id'],task_node='1:reorient'))
        self.assertEqual(self.e.used['calls'],calls);self.assertEqual(len(self.e.segments),count)

    def test_low_pose_unknown_objects_or_pending_retreat_never_authorize_rotation(self):
        self.prepare();snapshot=self.e.observe()
        self.assertTrue(rotation_clearance(snapshot,'red cube'))
        for mutate in [lambda s:s['tcp_xyz'].__setitem__(2,.1),
                       lambda s:s['objects']['green cube'].update(status='unknown'),
                       lambda s:s.update(recovery_options=dict(vertical_retreat=dict(status='available'))),
                       lambda s:s['tcp_xyz'].__setitem__(1,.4)]:
            state=copy.deepcopy(snapshot);mutate(state)
            self.assertFalse(rotation_clearance(state,'red cube'))

    def test_failed_alternatives_are_counted_and_cannot_be_reset_by_new_frames(self):
        self.prepare()
        with self.e.lock:
            for i in range(2):self.e.segments['alternative'+str(i)]=dict(id='alternative'+str(i),execution='failed',
                task_binding=dict(node_id='1:reorient',goal_version=1),node_verification=dict(status='unknown'))
        node=next(n for n in self.e.observe()['system']['task']['nodes'] if n['kind']=='reorient')
        self.assertEqual(node['status'],'blocked');self.assertEqual(node['reorientation_attempts'],2)
        with self.assertRaises(ValueError):compile_skill(dict(name='reorient_held_object',arguments=dict(object='red cube')),self.e.observe()['system'],self.e.observe())

    def test_not_advertised_without_opt_in_or_before_a_matching_release_failure(self):
        self.hold();system=self.e.observe()['system']
        self.assertNotIn('reorient_held_object',[x['name'] for x in system['skills']])
        self.assertFalse(any(n['kind']=='reorient' for n in system['task']['nodes']))
        self.prepare();self.e.segments.clear()
        node=next(n for n in self.e.observe()['system']['task']['nodes'] if n['kind']=='reorient')
        self.assertEqual(node['status'],'not_required')

    def test_idempotent_request_does_not_repeat_rotation(self):
        self.prepare();system=self.e.observe()['system']
        args=('same-alternative',self.e.revision,system['state_id'],'1:reorient',dict(name='reorient_held_object',arguments=dict(object='red cube')))
        sid=self.e.execute_skill(*args);until(lambda:self.e.active is None)
        moves=len(self.e.backend.moves);calls=self.e.used['calls']
        self.assertEqual(self.e.execute_skill(*args),sid)
        self.assertEqual(len(self.e.backend.moves),moves);self.assertEqual(self.e.used['calls'],calls)

    def test_goal_change_invalidates_old_reorientation_without_dispatch(self):
        self.prepare();system=self.e.observe()['system'];calls=self.e.used['calls']
        self.e.update_goal(dict(holding='green cube'))
        with self.assertRaises(ValueError):
            self.e.execute_skill('old-rotation',system['revision'],system['state_id'],'1:reorient',dict(name='reorient_held_object',arguments=dict(object='red cube')))
        self.assertEqual(self.e.used['calls'],calls);self.assertEqual(self.e.backend.moves,[])

    def test_stop_during_rotation_prevents_late_motion_and_holding_check(self):
        self.prepare();gate=threading.Event();entered=threading.Event();original=self.e.backend.tools
        def tools(segment):
            functions=original(segment);move=functions['goto_pose']
            def delayed(*args,**kwargs):
                entered.set();gate.wait(3);self.e.checkpoint(segment);return move(*args,**kwargs)
            functions['goto_pose']=delayed;return functions
        self.e.backend.tools=tools
        system=self.e.observe()['system']
        sid=self.e.execute_skill('stop-rotation',self.e.revision,system['state_id'],'1:reorient',dict(name='reorient_held_object',arguments=dict(object='red cube')))
        try:
            self.assertTrue(entered.wait(2));self.e.cancel()
        finally:gate.set()
        until(lambda:self.e.active is None)
        self.assertEqual(self.e.segments[sid]['execution'],'cancelled')
        self.assertEqual(self.e.backend.moves,[])
        self.assertNotIn('check_holding',[x['name'] for x in self.e.segments[sid]['api_attempts']])

    def test_two_distinct_yaws_then_third_alternative_is_denied(self):
        self.prepare()
        for index in range(2):
            system=self.e.observe()['system']
            sid=self.e.execute_skill('yaw-'+str(index),self.e.revision,system['state_id'],'1:reorient',dict(name='reorient_held_object',arguments=dict(object='red cube')))
            until(lambda:self.e.active is None)
            self.assertEqual(self.e.segments[sid]['execution'],'completed')
            snapshot=self.e.observe();system=snapshot['system']
            context=placement_context(snapshot,system['world']['facts'],dict(object='red cube',support='green cube'),self.e.segments)
            old=copy.deepcopy(self.e.segments['blocked-release']);old['id']='release-'+str(index);old['task_binding']['retry_context']=context
            with self.e.lock:self.e.segments[old['id']]=old
        node=next(n for n in self.e.observe()['system']['task']['nodes'] if n['kind']=='reorient')
        self.assertEqual(node['status'],'blocked');self.assertEqual(node['reorientation_attempts'],2)
        orientations=[m['quat'] for m in self.e.backend.moves]
        self.assertEqual(len(orientations),2)
        self.assertNotEqual(orientations[0],orientations[1])
        for q in orientations:self.assertAlmostEqual(math.degrees(2*math.acos(abs(q[2]))),25,places=4)

    def test_experimental_feature_does_not_break_real_backend_empty_gripper_verifier(self):
        import os
        from types import SimpleNamespace
        from unittest.mock import patch
        from agentic_cap.cap_backend import CaPBackend
        snapshot=dict(captured_at=time.time(),control_step=100,evidence_ref='frame-000100.npz',
            gripper_joint_positions=[.04,-.04],last_gripper_command='open')
        with patch.dict(os.environ,{'CAP_ALTERNATE_PLACEMENT':'1'}):
            result=CaPBackend.verify(SimpleNamespace(),dict(empty_gripper=True),snapshot)
        self.assertEqual(result['status'],'verified')
