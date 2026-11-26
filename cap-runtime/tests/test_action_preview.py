"""Read-only solver/projection checks; synthetic geometry is not robot success."""
import copy
import threading
import unittest
import numpy as np
from scipy.spatial.transform import Rotation
from agentic_cap.cartesian_motion import solve_pose_candidate,install_cartesian_motion
from agentic_cap.core import Halt
from agentic_cap.feedback import MotionError
from experiments.placement_preview import predict_obstruction
from test_cartesian_motion import Harness,solve,fk,P,Q


class IKPreviewTests(unittest.TestCase):
    def preview(self,h,solver=None,checkpoint=None):
        return solve_pose_candidate(P,Q,np.zeros(8),[0,0,-.107],solver or h.api_solver,h.backend.monitor.fk,
                                    checkpoint or (lambda:h.e.checkpoint(h.segment)))

    def test_preview_and_execution_share_same_refinement_without_motion_in_preview(self):
        h=Harness();calls=[]
        def solver(**kwargs):calls.append(copy.deepcopy(kwargs));return solve(**kwargs)
        initial=np.zeros(8);before=initial.copy()
        preview=solve_pose_candidate(P,Q,initial,[0,0,-.107],solver,fk,lambda:None)
        self.assertEqual(preview['status'],'accepted');self.assertEqual(preview['control_steps'],0)
        self.assertEqual(h.moves,[]);self.assertIsNone(h.e.target);np.testing.assert_equal(initial,before)
        # The existing executor invokes the same function and same first payload.
        h.fn(P,Q)
        np.testing.assert_allclose(calls[0]['target_pose_wxyz_xyz'],h.solves[0]['target_pose_wxyz_xyz'])
        self.assertEqual(len(h.moves),1)

    def test_rejection_is_bounded_and_does_not_assert_unreachability(self):
        calls=[]
        def inaccurate(**kwargs):
            calls.append(1);result=solve(**kwargs);result[0]+=.05;return result
        result=solve_pose_candidate(P,Q,np.zeros(8),[0,0,-.107],inaccurate,fk,lambda:None)
        self.assertEqual(result['status'],'rejected');self.assertEqual(len(calls),3)
        self.assertIsNone(result['configuration']);self.assertEqual(result['control_steps'],0)
        self.assertIn('not a proof',result['scope'])

    def test_late_solver_after_goal_change_cannot_return_accepted_preview(self):
        current=[True]
        def checkpoint():
            if not current[0]:raise Halt('Superseded')
        def late(**kwargs):current[0]=False;return solve(**kwargs)
        with self.assertRaises(Halt):solve_pose_candidate(P,Q,np.zeros(8),[0,0,-.107],late,fk,checkpoint)

    def test_bad_solver_and_callback_mutation_never_corrupt_returned_candidate(self):
        with self.assertRaises(MotionError):solve_pose_candidate(P,Q,np.zeros(8),[0,0,-.107],lambda **_:np.zeros(7),fk,lambda:None)
        result=solve_pose_candidate(P,Q,np.zeros(8),[0,0,-.107],solve,fk,lambda:None,
                                    lambda row:row.update(accepted=False,joint_solution=['bad']))
        self.assertEqual(result['status'],'accepted');self.assertTrue(result['ik_attempts'][0]['accepted'])


class ProjectionPreviewTests(unittest.TestCase):
    def packet(self):
        rgb=np.full((100,100,3),10,dtype=np.uint8);depth=np.ones((100,100,1),dtype=np.float32)
        rgb[40:60,40:60]=[10,220,10];rgb[65:80,40:60]=[220,10,10];depth[65:80,40:60]=.5
        return dict(rgb=rgb,depth=depth,intrinsics=np.array([[100,0,50],[0,100,50],[0,0,1]]),
            camera_pose_xyz_wxyz=np.array([0,0,0,1,0,0,0]),camera_name='test',evidence_ref='source',control_step=1,
            coordinate_frame='cap_robot_base',length_unit='m',quaternion_order='WXYZ')

    def preview(self,packet=None,candidate=None,mount='fixed'):
        return predict_obstruction(packet or self.packet(),dict(xyz=[0,0,0],wxyz=[1,0,0,0]),
            candidate or dict(xyz=[0,-.11,0],wxyz=[1,0,0,0]),'red cube','green cube',mount=mount)

    def test_known_projected_occlusion_is_a_prediction_not_a_runtime_fact(self):
        result=self.preview()
        self.assertEqual(result['status'],'predicted')
        self.assertGreater(result['predicted_obstructed_fraction'],.2)
        self.assertIn('never current evidence',result['authority'])
        self.assertNotIn('verified',result.values())

    def test_tcp_mount_moves_the_camera_with_the_same_rigid_transform(self):
        fixed=self.preview();wrist=self.preview(mount='tcp_mounted')
        self.assertEqual(fixed['future_camera_xyz'],[0.,0.,0.])
        np.testing.assert_allclose(wrist['future_camera_xyz'],[0.,-.11,0.])
        self.assertNotEqual(fixed['predicted_obstructed_fraction'],wrist['predicted_obstructed_fraction'])

    def test_unseen_support_or_bad_calibration_cannot_be_predicted_clear(self):
        missing=self.packet();missing['rgb'][40:60,40:60]=10
        bad=self.packet();bad['intrinsics'][0,0]=0
        for p in [missing,bad]:self.assertEqual(self.preview(p)['status'],'unknown')
        self.assertEqual(self.preview(mount='assumed')['status'],'unknown')

    def test_input_and_historical_evidence_are_not_mutated(self):
        packet=self.packet();before=copy.deepcopy(packet);self.preview(packet)
        for key in ['rgb','depth','intrinsics','camera_pose_xyz_wxyz']:np.testing.assert_equal(packet[key],before[key])
