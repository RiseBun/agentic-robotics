import copy
import time
import unittest
from agentic_cap.tabletop_sensors import verify,geometry,support_surface
import numpy as np


class EvidenceTests(unittest.TestCase):
    def data(self):
        now=time.time();o=dict(status='available',captured_at=now,surface_q02_xyz=[-.02,-.02,.85],
            surface_q98_xyz=[.02,.02,.89],surface_median_xyz=[0.,0.,.88])
        support=dict(o,surface_q02_xyz=[-.1,-.1,.8],surface_q98_xyz=[.1,.1,.85],surface_median_xyz=[0.,0.,.85])
        snapshot=dict(captured_at=now,control_step=20,evidence_ref='public',last_gripper_command='open',
            tcp_xyz=[0,0,1.1],gripper_joint_positions=[.04,.04],objects=dict(block_a=o,platform=support))
        history=[dict(copy.deepcopy(snapshot),captured_at=now-age,control_step=i) for i,age in enumerate([.8,.6,.4,.2,0])]
        for row in history:
            for obj in row['objects'].values():obj['captured_at']=row['captured_at']
        reference=dict(block_a=dict(o,observed_height_m=.04),platform=dict(support,observed_height_m=.05,surface_span_xyz=[.2,.2,.05]))
        return snapshot,history,reference
    def goal(self):return {'all':[dict(predicate='on',object='block_a',support='platform')]}
    def test_supported_visible_stable_relation_can_be_verified(self):
        s,h,r=self.data();self.assertEqual(verify(self.goal(),s,h,r)['status'],'verified')
    def test_unknown_support_never_counts_as_success(self):
        s,h,r=self.data();s['objects']['platform']['status']='unknown'
        self.assertEqual(verify(self.goal(),s,h,r)['status'],'unknown')
    def test_stale_object_geometry_is_not_refreshed_by_new_robot_packet(self):
        s,h,r=self.data();s['objects']['block_a']['captured_at']-=2
        self.assertEqual(verify(self.goal(),s,h,r)['status'],'unknown')
    def test_moving_object_does_not_pass_placement_acceptance(self):
        s,h,r=self.data();h[0]['objects']['block_a']['surface_median_xyz'][0]=.1
        self.assertEqual(verify(self.goal(),s,h,r)['status'],'unknown')
    def test_no_release_or_missing_height_is_unknown(self):
        s,h,r=self.data();s['last_gripper_command']='closed'
        self.assertEqual(verify(self.goal(),s,h,r)['status'],'unknown')
        s,h,r=self.data();r={}
        self.assertEqual(verify(self.goal(),s,h,r)['status'],'unknown')
    def test_short_temporal_window_is_unknown(self):
        s,h,r=self.data();self.assertEqual(verify(self.goal(),s,h[-2:],r)['status'],'unknown')
    def test_open_gripper_is_not_holding(self):
        s,h,r=self.data();self.assertEqual(verify({'all':[dict(predicate='holding',object='block_a')]},s,h,r)['status'],'failed')
    def test_current_custody_does_not_require_acquisition_lift_at_every_height(self):
        s,h,r=self.data();s['last_gripper_command']='closed';s['gripper_joint_positions']=[.02,.02];s['tcp_xyz']=[0,0,.9]
        for row in h:row.update(last_gripper_command='closed',gripper_joint_positions=[.02,.02],tcp_xyz=[0,0,.9])
        g={'all':[dict(predicate='holding',object='block_a')]}
        self.assertEqual(verify(g,s,h,r,dict(object='block_a'))['status'],'verified')
        self.assertEqual(verify(g,s,h,r)['status'],'failed')
        s['gripper_joint_positions']=[0.,0.]
        self.assertEqual(verify(g,s,h,r,dict(object='block_a'))['status'],'failed')

    def test_asynchronous_geometry_pairs_with_its_capture_pose_during_motion(self):
        s,h,r=self.data();s.update(last_gripper_command='closed',gripper_joint_positions=[.02,.02])
        for index,row in enumerate(h):
            # Actual measured offset is stable. The independently updated robot
            # packet has variable delay relative to the image capture pose.
            x=index*.03
            row['objects']['block_a']['surface_median_xyz']=[x,0,.99]
            row['geometry_robot_state']=dict(tcp_xyz=[x,0,1.],tcp_wxyz=[1.,0,0,0],control_step=index,captured_at=row['captured_at'])
            row['tcp_xyz']=[x+.04+index*.025,0,1.];row['last_gripper_command']='closed'
        s.update(h[-1]);s['gripper_joint_positions']=[.02,.02]
        result=verify({'all':[dict(predicate='holding',object='block_a')]},s,h,r,dict(object='block_a'))
        self.assertEqual(result['status'],'verified')
        metrics=result['conditions'][0]['metrics']
        self.assertAlmostEqual(metrics['relative_drift_m'],0.)
        self.assertEqual(metrics['pairing'],'image_capture_robot_state')
    def test_repeating_one_geometry_frame_cannot_create_a_holding_window(self):
        s,h,r=self.data();s.update(last_gripper_command='closed',gripper_joint_positions=[.02,.02])
        for row in h:
            row['geometry_robot_state']=dict(tcp_xyz=[0,0,.9],tcp_wxyz=[1.,0,0,0],control_step=1,captured_at=h[0]['captured_at'])
        self.assertEqual(verify({'all':[dict(predicate='holding',object='block_a')]},s,h,r,dict(object='block_a'))['status'],'unknown')
    def test_real_relative_object_motion_remains_unknown_with_synchronized_geometry(self):
        s,h,r=self.data();s.update(last_gripper_command='closed',gripper_joint_positions=[.02,.02])
        for index,row in enumerate(h):
            row['geometry_robot_state']=dict(tcp_xyz=[0,0,.9],tcp_wxyz=[1.,0,0,0],control_step=index,captured_at=row['captured_at'])
        h[0]['objects']['block_a']['surface_median_xyz'][0]=.06
        self.assertEqual(verify({'all':[dict(predicate='holding',object='block_a')]},s,h,r,dict(object='block_a'))['status'],'unknown')

    def test_observed_blocker_disables_top_grasp_without_a_task_recipe(self):
        s,h,r=self.data();s['objects']['block_b']=s['objects'].pop('platform');r['block_b']=r.pop('platform')
        for row in h:row['objects']['block_b']=row['objects'].pop('platform')
        result=verify({'all':[dict(predicate='accessible',object='block_b')]},s,h,r)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['conditions'][0]['blockers'],['block_a'])
    def test_missing_objects_do_not_prove_accessibility(self):
        s,h,r=self.data()
        self.assertEqual(verify({'all':[dict(predicate='accessible',object='block_a')]},s,h,r)['status'],'unknown')

    def test_measured_support_side_can_use_prior_public_size(self):
        current=dict(surface_q02_xyz=[.02,-.02,.8],surface_q98_xyz=[.021,.02,.82],surface_median_xyz=[.02,0,.81],plane_normal_xyz=[1,0,0],plane_residual_m=.0005,camera_center_world=[1,0,1.2])
        ref=dict(surface_span_xyz=[.04,.04,.04],observed_height_m=.04,captured_at=time.time()-5)
        result=support_surface(current,ref)
        self.assertIsNotNone(result);self.assertAlmostEqual(result['surface_q02_xyz'][0],-.02)
        self.assertAlmostEqual(result['surface_q98_xyz'][2],.84)
        self.assertIn('current_planar_side',result['completion']['method'])
    def test_two_missing_axes_and_nonplanar_partial_support_remain_unknown(self):
        current=dict(surface_q02_xyz=[.02,0,.8],surface_q98_xyz=[.021,.001,.82],surface_median_xyz=[.02,0,.81],plane_normal_xyz=[1,0,0],plane_residual_m=.0005,camera_center_world=[1,0,1.2])
        ref=dict(surface_span_xyz=[.04,.04,.04],observed_height_m=.04)
        self.assertIsNone(support_surface(current,ref))
        current['surface_q98_xyz'][1]=.04;current['plane_residual_m']=.02
        self.assertIsNone(support_surface(current,ref))
    def test_top_face_is_evidence_of_support_top_without_inventing_its_bottom(self):
        current=dict(surface_q02_xyz=[-.02,-.02,.84],surface_q98_xyz=[.02,.02,.8401],surface_median_xyz=[0,0,.84],plane_normal_xyz=[0,0,1],plane_residual_m=.0001,camera_center_world=[0,0,1.2])
        ref=dict(surface_span_xyz=[.04,.04,.04],observed_height_m=.04)
        result=support_surface(current,ref);self.assertAlmostEqual(result['surface_q98_xyz'][2],.8401)

    def region_data(self,center):
        s,h,r=self.data()
        for packet in [s,*h]:
            o=packet['objects']['block_a'];o.update(surface_q02_xyz=[center-.018,-.18,.8],surface_q98_xyz=[center+.018,-.14,.84],
                surface_median_xyz=[center,-.16,.83],surface_min_xyz=[center-.022,-.182,.8],surface_max_xyz=[center+.022,-.138,.84])
        r['block_a'].update(surface_span_xyz=[.044,.044,.04])
        return s,h,r
    def test_region_quantiles_inside_but_full_extent_near_edge_is_unknown(self):
        s,h,r=self.region_data(-.0601)
        goal={'all':[dict(predicate='in_region',object='block_a',region='left')]}
        self.assertEqual(verify(goal,s,h,r)['status'],'unknown')
    def test_region_with_measured_clearance_can_be_verified(self):
        s,h,r=self.region_data(-.12)
        goal={'all':[dict(predicate='in_region',object='block_a',region='left')]}
        self.assertEqual(verify(goal,s,h,r)['status'],'verified')
    def test_visible_region_violation_is_failed(self):
        s,h,r=self.region_data(-.05)
        goal={'all':[dict(predicate='in_region',object='block_a',region='left')]}
        self.assertEqual(verify(goal,s,h,r)['status'],'failed')

    def test_truncated_segmentation_cannot_provide_geometry(self):
        mask=np.ones((5,5),dtype=bool);camera=dict(rgb=np.zeros((5,5,3)),depth=np.ones((5,5)),intrinsics=np.eye(3),camera_to_world=np.eye(4),captured_at=time.time())
        self.assertEqual(geometry(camera,mask)['status'],'unknown')

if __name__=='__main__':unittest.main()
