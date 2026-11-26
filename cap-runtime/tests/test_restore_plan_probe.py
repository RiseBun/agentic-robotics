import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from experiments.recorded_restore_probe import probe_retreat
from agentic_cap.core import Halt

class ProbeTests(unittest.TestCase):
    def data(self):
        e=SimpleNamespace(checkpoint=Mock(),observe=Mock(return_value={}),event=Mock())
        b=SimpleNamespace(episode=e,verify=Mock(side_effect=[{'blockers':['scene_versions_match']},{'status':'verified'}]),
            _plan_pose=Mock(return_value=SimpleNamespace(joint_positions=[[0]*7]*3,collision_aware=True)),_move_trajectory=Mock())
        plan=dict(object='block_a',targets={'approach':([0,0,1],[0,0,1,0])})
        return b,plan
    def test_planning_is_not_motion(self):
        b,p=self.data();r=probe_retreat(b,{},p)
        self.assertEqual(r['status'],'planned');self.assertFalse(r['executed']);b._move_trajectory.assert_not_called()
    def test_other_failed_guard_prevents_even_planning(self):
        b,p=self.data();b.verify.side_effect=[{'blockers':['encoder_at_endpoint']},{'status':'verified'}]
        self.assertEqual(probe_retreat(b,{},p)['status'],'not_attempted');b._plan_pose.assert_not_called()
    def test_unknown_custody_prevents_planning(self):
        b,p=self.data();b.verify.side_effect=[{'blockers':['scene_versions_match']},{'status':'unknown'}]
        self.assertEqual(probe_retreat(b,{},p)['status'],'not_attempted');b._plan_pose.assert_not_called()
    def test_cancellation_is_not_converted_to_planner_failure(self):
        b,p=self.data();b._plan_pose.side_effect=Halt('stop','CANCELLED')
        with self.assertRaises(Halt):probe_retreat(b,{},p)
        b._move_trajectory.assert_not_called()
if __name__=='__main__':unittest.main()
