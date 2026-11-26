"""Retry constraints are protocol guards, not physical performance measurements."""
import copy
import tempfile
import time
import unittest
from agentic_cap.core import Manager
from agentic_cap.tabletop_domain import digest
from agentic_cap.tabletop_fixture import TabletopFixtureBackend
from agentic_cap.tabletop_recovery import context_for,constraint,RecoveryBlocked

PLACE=dict(name='place_object',arguments=dict(object='block_a',support='platform'))
class RetryContextTests(unittest.TestCase):
    def context(self,segments=None,versions=None,skill=None,goal=1):
        return context_for(skill or PLACE,goal,dict(object_versions=versions or {'block_a':1,'platform':1,'block_b':1}),segments or {})
    def failed(self,key,ident='failure'):
        return dict(id=ident,result_revision=1,execution='failed',api_attempts=[dict(name='set_gripper',status='failed')],
            task_binding=dict(goal_version=1,recovery_context_key=digest(key),recovery_operation=copy.deepcopy(PLACE)))
    def test_two_failures_block_same_operation(self):
        ctx=self.context();segments={str(i):self.failed(ctx,str(i)) for i in range(2)}
        self.assertTrue(constraint(PLACE,ctx,segments)['blocked'])
        self.assertFalse(constraint(PLACE,ctx,{'a':segments['0']})['blocked'])
    def test_cancel_inflight_and_success_do_not_count_as_failed(self):
        ctx=self.context();s=self.failed(ctx)
        for kind in ('cancel','running','success','no_api'):
            with self.subTest(kind=kind):
                row=copy.deepcopy(s)
                if kind=='cancel':row['execution']='cancelled'
                if kind=='running':row.pop('result_revision')
                if kind=='success':row['node_verification']={'status':'verified'}
                if kind=='no_api':row['api_attempts']=[]
                self.assertIsNone(constraint(PLACE,ctx,{'a':row}))
    def test_carried_object_or_unrelated_motion_cannot_rearm_placement(self):
        self.assertEqual(self.context(),self.context(versions={'block_a':99,'platform':1,'block_b':99}))
    def test_support_change_new_grasp_or_new_goal_rearm_context(self):
        ctx=self.context()
        self.assertNotEqual(ctx,self.context(versions={'block_a':1,'platform':2}))
        grasp=dict(id='new-grasp',result_revision=1,execution='completed',node_verification={'status':'verified'},
            task_binding={'goal_version':1},skill=dict(name='acquire_object',arguments={'object':'block_a'}))
        self.assertNotEqual(ctx,self.context({'g':grasp}))
        self.assertNotEqual(ctx,self.context(goal=2))
        grasp['node_verification']['status']='unknown'
        self.assertEqual(ctx,self.context({'g':grasp}))
    def test_restoration_passive_or_unknown_inspection_does_not_rearm(self):
        ctx=self.context()
        for kind,views,status in [('restore_grip_pose',1,'available'),('inspect_object',0,'available'),('inspect_object',2,'unknown')]:
            s=dict(id='s',result_revision=1,task_binding={'goal_version':1},skill=dict(name=kind,arguments={'object':'block_a'}),
                api_attempts=[dict(name='inspect_object',status='completed',result=dict(status=status,views_used=views))])
            self.assertEqual(ctx,self.context({'s':s}))
    def test_only_relevant_actual_available_view_changes_context(self):
        ctx=self.context();s=dict(id='view',result_revision=1,task_binding={'goal_version':1},
            skill=dict(name='inspect_object',arguments={'object':'platform'}),
            api_attempts=[dict(name='inspect_object',status='completed',result=dict(status='available',views_used=1))])
        self.assertNotEqual(ctx,self.context({'s':s}))
        s['skill']['arguments']['object']='block_b'
        self.assertEqual(ctx,self.context({'s':s}))
        s['skill']['arguments']['object']='platform';s['task_binding']['goal_version']=0
        self.assertEqual(ctx,self.context({'s':s}))
    def test_different_destination_is_a_different_operation(self):
        other=dict(name='place_in_region',arguments=dict(object='block_a',region='left'))
        self.assertNotEqual(self.context(),self.context(skill=other))

class RetryAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.manager=Manager(self.temp.name,TabletopFixtureBackend)
        s=self.manager.create('retry',scene='tabletop_v1',seed=201,goal={'all':[dict(predicate='on',object='block_a',support='platform'),dict(predicate='empty_gripper')]})
        self.e=self.manager.episodes[s['id']];self.serial=0
        self.until(lambda:self.e.snapshot and self.e.status=='ready')
    def tearDown(self):self.e.close();self.temp.cleanup()
    def until(self,fn):
        end=time.monotonic()+5
        while time.monotonic()<end:
            if fn():return
            time.sleep(.01)
        self.fail('Timed out')
    def plan(self,skill):
        self.serial+=1
        self.e.submit_plan('p'+str(self.serial),self.e.revision,{'nodes':[dict(id='renamed'+str(self.serial),skill=skill)]})
        s=self.e.observe()['system'];return s,s['task']['nodes'][0]
    def execute(self,skill):
        s,n=self.plan(skill)
        sid=self.e.execute_skill('s'+str(self.serial),self.e.revision,s['state_id'],n['id'],skill)
        self.until(lambda:self.e.state()['active_segment'] is None)
        return sid,self.e.state()['segments'][sid],s,n
    def failed_place(self):
        self.e.backend.refuse_release=True
        sid,seg,s,n=self.execute(PLACE)
        self.assertNotEqual(seg.get('node_verification',{}).get('status'),'verified')
        return sid,seg,s,n
    def test_replanning_restoration_code_and_old_reference_cannot_bypass_guard(self):
        self.execute(dict(name='acquire_object',arguments={'object':'block_a'}))
        first=self.failed_place()
        self.execute(dict(name='restore_grip_pose',arguments={'object':'block_a'}))
        second=self.failed_place()
        s,n=self.plan(PLACE)
        self.assertEqual(n['status'],'blocked');self.assertEqual(n['retry_constraint']['failures'],2)
        calls=self.e.used['calls'];motion=self.e.used['motion_steps'];segments=len(self.e.segments)
        with self.assertRaises(RecoveryBlocked):self.e.execute_skill('blocked',self.e.revision,s['state_id'],n['id'],PLACE)
        with self.assertRaises(RecoveryBlocked):self.e.execute('code',self.e.revision,'different wording','p=plan_place("block_a",support="platform")',task_ref=dict(state_id=s['state_id'],task_node=n['id']))
        self.assertEqual(self.e.used['calls'],calls);self.assertEqual(self.e.used['motion_steps'],motion);self.assertEqual(len(self.e.segments),segments)
        # Exact request replay still returns its original ID without dispatch.
        sid,seg,old,oldnode=second
        self.assertEqual(self.e.execute_skill('s'+str(self.serial-1),self.e.revision,old['state_id'],oldnode['id'],PLACE),sid)
        # Passive inspection and new plan IDs cannot clear two failures.
        self.execute(dict(name='inspect_object',arguments={'object':'platform','evidence':'geometry'}))
        self.assertTrue(self.plan(PLACE)[1]['retry_constraint']['blocked'])
        # Relevant successful physical view action creates an independent retry context.
        self.e.backend.occluded=True;self.e.backend.restore_after_view=True
        self.execute(dict(name='inspect_object',arguments={'object':'platform','evidence':'geometry'}))
        n=self.plan(PLACE)[1];self.assertNotIn('retry_constraint',n)
        self.assertEqual(self.e.backend.reset_count,1)
    def test_direct_cap_envelope_also_enforces_runtime_guard(self):
        self.e.close()
        s=self.manager.create('direct-retry',scene='tabletop_v1',seed=201,goal={'all':[dict(predicate='holding',object='block_a')]},policy='direct_cap')
        self.e=self.manager.episodes[s['id']];self.until(lambda:self.e.snapshot and self.e.status=='ready')
        skill=dict(name='acquire_object',arguments={'object':'block_a'});self.e.backend.missed=True
        from agentic_cap.tabletop_domain import compile_skill
        for i in range(2):
            env=self.e.submit_operation('env'+str(i),self.e.revision,skill)
            code,contract=compile_skill(skill,self.e.robot_system.catalog)
            self.e.execute('try'+str(i),self.e.revision,'direct',code,contract,env['task_ref'])
            self.until(lambda:self.e.state()['active_segment'] is None)
        env=self.e.submit_operation('blocked-env',self.e.revision,skill)
        with self.assertRaises(RecoveryBlocked):self.e.execute('blocked-code',self.e.revision,'direct',code,contract,env['task_ref'])

if __name__=='__main__':unittest.main()
