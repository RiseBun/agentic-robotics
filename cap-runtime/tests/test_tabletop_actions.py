import threading
import time
import unittest
import numpy as np
from types import SimpleNamespace
from unittest.mock import Mock
from agentic_cap.tabletop_actions import tools
from agentic_cap.tabletop_backend import TabletopBackend


class ActionBoundaryTests(unittest.TestCase):
    def backend(self,name='acquire_object',args=None):
        args=args or dict(object='block_a');segment=dict(id='s',revision=1,task_binding=dict(node_id='1:p1_action'))
        node=dict(id='1:p1_action',skills=[name],arguments=args)
        state=dict(system=dict(task=dict(nodes=[node])),tcp_xyz=[0,0,1.],tcp_wxyz=[0,1,0,0],evidence_ref='public-frame')
        episode=SimpleNamespace(checkpoint=Mock(),observe=lambda:state,event=Mock(),revision=1,used=dict(calls=0,motion_steps=0))
        b=SimpleNamespace(episode=episode,object_versions={'block_a':0},plans={},last_plan=None,held_hint=None,inspection_sessions={},verify=Mock(return_value=dict(status='unknown')), _plan_pose=Mock(),_move_trajectory=Mock())
        return b,segment
    def test_code_cannot_grasp_an_object_outside_current_node(self):
        b,s=self.backend()
        with self.assertRaisesRegex(ValueError,'outside admitted'):tools(b,s)['plan_grasp']('block_b')
        b._plan_pose.assert_not_called()
    def test_code_cannot_release_without_ordered_plan(self):
        b,s=self.backend()
        with self.assertRaisesRegex(ValueError,'No admitted gripper'):tools(b,s)['set_gripper']('open')
        b._move_trajectory.assert_not_called()
    def test_old_plan_cannot_dispatch_after_goal_change(self):
        b,s=self.backend();b.plans['old']=dict(segment='s',revision=0)
        with self.assertRaisesRegex(ValueError,'stale'):tools(b,s)['execute_stage']('old','approach')
        b._plan_pose.assert_not_called()
    def test_model_cannot_replace_effect_verifier(self):
        b,s=self.backend()
        with self.assertRaisesRegex(ValueError,'Cannot replace'):tools(b,s)['verify_effect']({'all':[dict(predicate='empty_gripper')]})
    def test_unknown_holding_blocks_inspection_motion(self):
        b,s=self.backend('inspect_object');b.held_hint='block_b';b.episode.snapshot=dict(evidence_ref='public')
        result=tools(b,s)['inspect_object']('block_a')
        self.assertEqual(result['status'],'unknown');b._plan_pose.assert_not_called()
    def test_visible_object_needs_no_inspection_motion(self):
        b,s=self.backend('inspect_object');b.verify.return_value=dict(status='verified');b.episode.snapshot=dict(evidence_ref='current')
        result=tools(b,s)['inspect_object']('block_a')
        self.assertEqual(result['views_used'],0);self.assertEqual(result['status'],'available');b._plan_pose.assert_not_called()
    def test_recorded_reverse_path_expires_with_goal_scene_or_encoder_change(self):
        b=object.__new__(TabletopBackend);b.episode=SimpleNamespace(revision=1);b.encoder=np.zeros(7)
        trajectory=SimpleNamespace(joint_positions=np.zeros((2,7)))
        b.recorded_descent=dict(object='block_a',revision=1,trajectory=trajectory,other_versions={'platform':0})
        g={'all':[dict(predicate='recorded_descent',object='block_a')]}
        snapshot=dict(last_gripper_command='closed',object_versions={'platform':0})
        self.assertEqual(b.verify(g,snapshot)['status'],'verified')
        for mutated in [dict(snapshot,last_gripper_command='open'),dict(snapshot,object_versions={'platform':1})]:
            self.assertEqual(b.verify(g,mutated)['status'],'unknown')
        b.episode.revision=2;self.assertEqual(b.verify(g,snapshot)['status'],'unknown')
        b.episode.revision=1;b.encoder=np.ones(7);self.assertEqual(b.verify(g,snapshot)['status'],'unknown')

    def test_unknown_monitor_evidence_is_not_a_confirmed_slip(self):
        b=object.__new__(TabletopBackend);b.holding_watch=dict(object='block_a',failures=0);b.verify=Mock(return_value=dict(status='unknown'));b.tick=10
        self.assertEqual(b.poll_holding_watchdog(dict(last_gripper_command='closed'))['status'],'unknown')
        b.verify.return_value=dict(status='failed')
        for _ in range(2):self.assertEqual(b.poll_holding_watchdog(dict(last_gripper_command='closed'))['status'],'unknown')
        self.assertEqual(b.poll_holding_watchdog(dict(last_gripper_command='closed'))['status'],'violated')

    def test_release_rechecks_destination_geometry_and_does_not_open_on_unknown(self):
        for destination in ({'support':'platform'},{'region':'left'}):
            with self.subTest(destination=destination):
                b,s=self.backend('place_object',dict(object='block_a',**destination));b.tick=0
                b._geometry_packet=Mock(return_value=({}, {}, {'status':'available'}))
                b.wait_public_effect=Mock(return_value={'status':'unknown'})
                b.episode.gripper=0.;b.episode.snapshot={'evidence_ref':'current'}
                class AdvancingCV:
                    def __enter__(self):return self
                    def __exit__(self,*args):return False
                    def wait(self,_seconds):b.tick+=20
                b.episode.cv=AdvancingCV()
                b.last_plan='place';b.plans['place']=dict(segment='s',kind='place',object='block_a',sequence=['open'],cursor=0,**destination)
                with self.assertRaisesRegex(ValueError,'destination evidence'):tools(b,s)['set_gripper']('open')
                self.assertEqual(b.episode.gripper,0.);self.assertEqual(b.plans['place']['cursor'],0)
                conditions=b.wait_public_effect.call_args.args[1]['all']
                self.assertIn(dict(predicate='holding',object='block_a'),conditions)
                expected=dict(predicate='support_geometry',object='platform') if 'support' in destination else dict(predicate='region',region='left')
                self.assertIn(expected,conditions)

if __name__=='__main__':unittest.main()
