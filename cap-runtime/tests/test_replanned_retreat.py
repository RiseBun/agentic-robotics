import os,copy,unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
import numpy as np
from agentic_cap.tabletop_domain import validate_skill,compile_skill,enabled_skills
from agentic_cap.tabletop_scene import public_catalog
from agentic_cap.tabletop_actions import tools
from agentic_cap.tabletop_backend import TabletopBackend
from agentic_cap.tabletop_recovery import context_for,constraint
from agentic_cap.core import Halt

class ReplannedRetreatTests(unittest.TestCase):
    def backend(self):
        s=dict(id='s',revision=1,task_binding=dict(node_id='1:p1_recover'))
        state=dict(system=dict(task=dict(nodes=[dict(id='1:p1_recover',skills=['replan_grip_pose'],arguments={'object':'block_a'})])),
            object_versions={'platform':1,'block_a':1},evidence_ref='public-current')
        e=SimpleNamespace(checkpoint=Mock(),observe=lambda:state,event=Mock(),revision=1,gripper=0.)
        b=SimpleNamespace(episode=e,encoder=np.zeros(7),tick=0,
            recorded_descent=dict(object='block_a',revision=1,retreat_target=dict(xyz=[.1,.1,1.],wxyz=[0,0,1,0])),
            _plan_pose=Mock(return_value=SimpleNamespace(collision_aware=True)),_move_trajectory=Mock(),wait_public_effect=Mock(return_value={'status':'verified'}))
        def verify(goal,snapshot):
            pred=goal['all'][0]['predicate']
            if pred=='recorded_descent':return {'status':'unknown','blockers':['scene_versions_match']}
            if pred=='recorded_retreat_target':return {'status':'verified' if e.revision==1 else 'unknown'}
            return {'status':'verified'}
        b.verify=Mock(side_effect=verify)
        class CV:
            def __enter__(self):return self
            def __exit__(self,*_):return False
            def wait(self,_):b.tick+=25
        e.cv=CV();return b,s,state
    def test_feature_disabled_rejects_plan_and_is_not_advertised(self):
        with patch.dict(os.environ,{'TABLETOP_REPLANNED_RETREAT':'0'}):
            self.assertNotIn('replan_grip_pose',enabled_skills())
            with self.assertRaises(ValueError):validate_skill({'name':'replan_grip_pose','arguments':{'object':'block_a'}},public_catalog())
    def test_enabled_compiles_one_bound_operation_without_model_coordinates(self):
        with patch.dict(os.environ,{'TABLETOP_REPLANNED_RETREAT':'1'}):
            code,contract=compile_skill({'name':'replan_grip_pose','arguments':{'object':'block_a'}},public_catalog())
            self.assertEqual(code,"replan_grip_pose('block_a')")
            self.assertEqual(contract['postcondition'],{'all':[{'predicate':'holding','object':'block_a'}]})
            with self.assertRaises(ValueError):validate_skill({'name':'replan_grip_pose','arguments':{'object':'block_a','xyz':[0,0,1]}},public_catalog())
    def test_fresh_trajectory_executes_without_using_old_reverse_path_or_opening_grip(self):
        b,s,state=self.backend();old=b.recorded_descent
        result=tools(b,s)['replan_grip_pose']('block_a')
        self.assertEqual(result['status'],'verified');b._move_trajectory.assert_called_once_with(s,b._plan_pose.return_value)
        np.testing.assert_equal(b._plan_pose.call_args.args[1],[.1,.1,1.]);self.assertEqual(b._plan_pose.call_args.kwargs,{'exclude':'block_a'})
        self.assertIsNone(b.recorded_descent);self.assertEqual(b.episode.gripper,0.)
        self.assertEqual(b.restoration_done['object'],'block_a')
    def test_unknown_current_holding_prevents_planning(self):
        b,s,state=self.backend();original=b.verify.side_effect
        b.verify.side_effect=lambda g,x:({'status':'unknown'} if g['all'][0]['predicate']=='holding' else original(g,x))
        with self.assertRaisesRegex(ValueError,'custody'):tools(b,s)['replan_grip_pose']('block_a')
        b._plan_pose.assert_not_called();b._move_trajectory.assert_not_called()
    def test_scene_change_during_planning_prevents_dispatch(self):
        b,s,state=self.backend()
        def plan(*a,**k):state['object_versions']['platform']=2;return SimpleNamespace()
        b._plan_pose.side_effect=plan
        with self.assertRaisesRegex(ValueError,'scene changed'):tools(b,s)['replan_grip_pose']('block_a')
        b._move_trajectory.assert_not_called()
    def test_goal_change_during_planning_prevents_dispatch(self):
        b,s,state=self.backend()
        def plan(*a,**k):b.episode.revision=2;return SimpleNamespace()
        b._plan_pose.side_effect=plan
        with self.assertRaisesRegex(ValueError,'binding changed'):tools(b,s)['replan_grip_pose']('block_a')
        b._move_trajectory.assert_not_called()
    def test_custody_lost_during_planning_prevents_dispatch(self):
        b,s,state=self.backend();lost=False;original=b.verify.side_effect
        def plan(*a,**k):
            nonlocal lost
            lost=True;return SimpleNamespace()
        b._plan_pose.side_effect=plan
        b.verify.side_effect=lambda g,x:({'status':'unknown'} if lost and g['all'][0]['predicate']=='holding' else original(g,x))
        with self.assertRaisesRegex(ValueError,'Custody unknown'):tools(b,s)['replan_grip_pose']('block_a')
        b._move_trajectory.assert_not_called()
    def test_cancellation_after_planning_prevents_dispatch(self):
        b,s,state=self.backend()
        b._plan_pose.side_effect=lambda *a,**k:setattr(b.episode.checkpoint,'side_effect',Halt('stop','CANCELLED'))
        with self.assertRaises(Halt):tools(b,s)['replan_grip_pose']('block_a')
        b._move_trajectory.assert_not_called()
    def test_old_reverse_path_stays_blocked_while_target_can_be_replanned(self):
        b=object.__new__(TabletopBackend);b.episode=SimpleNamespace(revision=1);b.encoder=np.zeros(7)
        b.recorded_descent=dict(object='block_a',revision=1,trajectory=SimpleNamespace(joint_positions=np.zeros((2,7))),other_versions={'platform':0},retreat_target={'xyz':[0,0,1],'wxyz':[0,0,1,0]})
        state=dict(last_gripper_command='closed',object_versions={'platform':1})
        with patch.dict(os.environ,{'TABLETOP_REPLANNED_RETREAT':'1'}):
            self.assertEqual(b.verify({'all':[{'predicate':'recorded_descent','object':'block_a'}]},state)['status'],'unknown')
            self.assertEqual(b.verify({'all':[{'predicate':'recorded_retreat_target','object':'block_a'}]},state)['status'],'verified')
            b.episode.revision=2
            self.assertEqual(b.verify({'all':[{'predicate':'recorded_retreat_target','object':'block_a'}]},state)['status'],'unknown')
    def test_unchanged_failed_replans_share_retry_constraint(self):
        skill={'name':'replan_grip_pose','arguments':{'object':'block_a'}};state={'object_versions':{'platform':1,'block_a':1}}
        c=context_for(skill,1,state,{});from agentic_cap.tabletop_domain import digest
        segments={str(i):dict(id=str(i),task_binding={'recovery_context_key':digest(c)},result_revision=1,api_attempts=[{'name':'replan_grip_pose','status':'failed'}],node_verification={'status':'unknown'}) for i in range(2)}
        self.assertTrue(constraint(skill,c,segments)['blocked'])
if __name__=='__main__':unittest.main()
