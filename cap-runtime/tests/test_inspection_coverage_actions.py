"""Coverage remains bounded by execution, custody and cancellation guards."""
import unittest,time
from unittest.mock import Mock
from types import SimpleNamespace
import numpy as np
import test_tabletop_actions as fixture
from agentic_cap.tabletop_actions import tools
from agentic_cap.core import Halt

class CoverageActionTests(unittest.TestCase):
    def backend(self):
        b,segment=fixture.ActionBoundaryTests().backend('inspect_object',{'object':'platform','evidence':'support_geometry'})
        state=b.episode.observe();state['tcp_xyz']=[0,0,1.]
        b.episode.robot_system=SimpleNamespace(goal_version=1);b.episode.snapshot=state
        b.inspection_policy='coverage';b.inspection_anchors={};b.baselines={'platform':np.array([.3,.3,1.2])}
        b.reference={'platform':dict(surface_median_xyz=[0,0,.83],surface_span_xyz=[.08,.08,.03])}
        T=np.diag([1.,-1.,-1.,1.]);T[2,3]=1.
        b.raw=dict(captured_at=time.time(),tcp_xyz=[0,0,1.],cameras={'wrist':dict(captured_at=time.time(),
            depth=np.ones((240,320)),intrinsics=np.array([[200.,0,160],[0,200.,120],[0,0,1]]),camera_to_world=T)})
        b.tick=0
        class CV:
            def __enter__(self):return self
            def __exit__(self,*_):return False
            def wait(self,_):b.tick+=25
        b.episode.cv=CV()
        def verify(goal,_snapshot):
            return {'status':'verified' if goal['all'][0]['predicate']=='empty_gripper' else 'unknown'}
        b.verify.side_effect=verify
        return b,segment
    def test_fixed_reference_is_aiming_prior_and_at_most_two_movements(self):
        b,s=self.backend();result=tools(b,s)['inspect_object']('platform','support_geometry')
        self.assertEqual(result['views_used'],2);self.assertEqual(result['status'],'unknown')
        self.assertEqual(b._plan_pose.call_count,2);self.assertEqual(b._move_trajectory.call_count,2)
        inputs=[call.kwargs for call in b.episode.event.call_args_list if call.args[0]=='inspection_selection_inputs']
        self.assertEqual(inputs[0]['aim_xyz'],[0.,0.,.83])
        self.assertEqual(len(b.inspection_sessions['platform']),2)
    def test_unknown_empty_gripper_never_moves(self):
        b,s=self.backend();b.verify.side_effect=None;b.verify.return_value={'status':'unknown'}
        self.assertEqual(tools(b,s)['inspect_object']('platform','support_geometry')['views_used'],0)
        b._plan_pose.assert_not_called()
    def test_cancel_before_movement_cannot_dispatch_late_candidate(self):
        b,s=self.backend();b.episode.checkpoint.side_effect=Halt('stop','CANCELLED')
        with self.assertRaises(Halt):tools(b,s)['inspect_object']('platform','support_geometry')
        b._plan_pose.assert_not_called();b._move_trajectory.assert_not_called()
    def test_failed_plans_still_count_toward_two_attempt_limit(self):
        b,s=self.backend();b._plan_pose.side_effect=ValueError('collision')
        result=tools(b,s)['inspect_object']('platform','support_geometry')
        self.assertEqual(result['views_used'],2);self.assertEqual(result['status'],'unknown')
        b._move_trajectory.assert_not_called();self.assertEqual(b._plan_pose.call_count,2)
    def test_low_held_pose_reports_restore_requirement_without_implicit_motion(self):
        b,s=self.backend();state=b.episode.observe();state['tcp_xyz']=[0,0,.87];b.held_hint='block_a'
        def verify(goal,_snapshot):return {'status':'verified' if goal['all'][0]['predicate']=='holding' else 'unknown'}
        b.verify.side_effect=verify
        result=tools(b,s)['inspect_object']('platform','support_geometry')
        self.assertIn('requires_recorded_restore',result['reason']);self.assertEqual(result['views_used'],0)
        b._plan_pose.assert_not_called();b._move_trajectory.assert_not_called()
        self.assertEqual(b.episode.event.call_args_list[-1].args[0],'evidence_acquisition_finished')

if __name__=='__main__':unittest.main()
