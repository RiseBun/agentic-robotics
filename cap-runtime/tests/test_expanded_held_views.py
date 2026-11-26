import unittest,copy,time
from unittest.mock import Mock
import numpy as np
import test_tabletop_views as view_fixture
import test_inspection_coverage_actions as action_fixture
from agentic_cap.tabletop_views import rank_views,inspection_anchor_key
from agentic_cap.tabletop_actions import tools
from agentic_cap.core import Halt

class ExpandedHeldViewTests(unittest.TestCase):
    def backend(self):
        b,s=action_fixture.CoverageActionTests().backend();b.expanded_held_views=True;b.held_hint='block_a'
        state=b.episode.observe();state['object_versions']={'platform':1,'block_a':1}
        def verify(goal,_):return {'status':'verified' if goal['all'][0]['predicate']=='holding' else 'unknown'}
        b.verify=Mock(side_effect=verify)
        return b,s,state
    def test_default_holding_retains_original_two_y_only_views(self):
        p=view_fixture.ViewTests().packet();rows=rank_views(p,[0,0,1],[0,0,.83],[.08,.08,.03],holding=True)
        self.assertEqual(len(rows),2)
        self.assertTrue(all(r['target_xyz'][0]==0 and r['target_xyz'][2]==1 for r in rows))
    def test_expansion_uses_same_bounded_family_as_empty_grip(self):
        p=view_fixture.ViewTests().packet();args=(p,[0,0,1],[0,0,.83],[.08,.08,.03])
        empty=rank_views(*args,holding=False);held=rank_views(*args,holding=True,expanded_holding=True)
        self.assertEqual({tuple(x['signature']) for x in empty},{tuple(x['signature']) for x in held})
        self.assertGreater(len(held),2)
        for row in held:
            x,y,z=row['target_xyz'];self.assertLessEqual(abs(x),.08);self.assertLessEqual(abs(y),.08);self.assertIn(z,[1.,1.06])
            self.assertNotIn('target_wxyz',row)
    def test_expanded_planning_excludes_only_current_held_object_and_keeps_two_attempts(self):
        b,s,state=self.backend();result=tools(b,s)['inspect_object']('platform','support_geometry')
        self.assertEqual(result['views_used'],2);self.assertEqual(b._move_trajectory.call_count,2)
        self.assertTrue(all(c.kwargs=={'exclude':'block_a'} for c in b._plan_pose.call_args_list))
    def test_unknown_holding_does_not_dispatch_or_plan(self):
        b,s,state=self.backend();b.verify.side_effect=None;b.verify.return_value={'status':'unknown'}
        result=tools(b,s)['inspect_object']('platform','support_geometry')
        self.assertEqual(result['views_used'],0);b._plan_pose.assert_not_called();b._move_trajectory.assert_not_called()
    def test_custody_lost_during_planning_prevents_motion(self):
        b,s,state=self.backend();lost=False;original=b.verify.side_effect
        def plan(*a,**k):
            nonlocal lost
            lost=True;return Mock()
        b._plan_pose.side_effect=plan;b.verify.side_effect=lambda g,x:({'status':'unknown'} if lost and g['all'][0]['predicate']=='holding' else original(g,x))
        result=tools(b,s)['inspect_object']('platform','support_geometry')
        self.assertEqual(result['reason'],'custody_changed_during_inspection_planning');b._move_trajectory.assert_not_called()
    def test_scene_change_during_planning_prevents_motion(self):
        b,s,state=self.backend()
        def plan(*a,**k):state['object_versions']['platform']=2;return Mock()
        b._plan_pose.side_effect=plan;result=tools(b,s)['inspect_object']('platform','support_geometry')
        self.assertEqual(result['reason'],'scene_changed_during_inspection_planning');b._move_trajectory.assert_not_called()
    def test_cancel_after_planning_prevents_motion(self):
        b,s,state=self.backend()
        def plan(*a,**k):b.episode.checkpoint.side_effect=Halt('stop','CANCELLED');return Mock()
        b._plan_pose.side_effect=plan
        with self.assertRaises(Halt):tools(b,s)['inspect_object']('platform','support_geometry')
        b._move_trajectory.assert_not_called()
    def test_rejected_plans_still_consume_two_attempts(self):
        b,s,state=self.backend();b._plan_pose.side_effect=ValueError('collision')
        result=tools(b,s)['inspect_object']('platform','support_geometry')
        self.assertEqual(result['views_used'],2);self.assertEqual(b._plan_pose.call_count,2);b._move_trajectory.assert_not_called()
    def test_completed_restore_reanchors_old_low_inspection_without_encoder_drift_reset(self):
        b,s,state=self.backend();state['tcp_xyz']=[0,0,.87];b.restoration_done=None
        first=tools(b,s)['inspect_object']('platform','support_geometry')
        self.assertEqual(first['reason'],'low_held_pose_requires_recorded_restore_before_inspection')
        self.assertEqual(first['views_used'],0)
        state['tcp_xyz']=[0,0,1.];b.restoration_done={'object':'block_a','revision':1,'joints':[0]*7}
        second=tools(b,s)['inspect_object']('platform','support_geometry')
        self.assertEqual(second['views_used'],2)
        keys=list(b.inspection_anchors);self.assertEqual(len(keys),2)
        state['tcp_xyz']=[.0001,0,1.0002]
        third=tools(b,s)['inspect_object']('platform','support_geometry')
        self.assertEqual(list(b.inspection_anchors),keys)
        self.assertTrue(all(k in keys for k in b.inspection_anchors))
    def test_anchor_key_depends_on_stored_restore_not_current_encoders(self):
        r={'object':'block_a','revision':1,'joints':[0]*7}
        a=inspection_anchor_key(1,'platform',r);self.assertEqual(a,inspection_anchor_key(1,'platform',copy.deepcopy(r)))
        self.assertNotEqual(a,inspection_anchor_key(1,'platform'))
        self.assertNotEqual(a,inspection_anchor_key(2,'platform',r))
if __name__=='__main__':unittest.main()
