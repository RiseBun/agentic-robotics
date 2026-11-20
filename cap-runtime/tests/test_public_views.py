import threading
import copy
import time
import unittest
from types import SimpleNamespace
import numpy as np
from agentic_cap.public_views import read_camera_packet, choose_support_view, fuse_public_surface, camera_snapshot
from agentic_cap.public_sensors import COORDINATE_CONVENTION
from agentic_cap.active_evidence import support_evidence, _single_support_evidence


class PublicViewTests(unittest.TestCase):
    def backend(self):
        low = SimpleNamespace(_render_width=12, _render_height=8, render_camera_names=['robot0_robotview'])
        def render(**kwargs):
            self.assertEqual(kwargs['camera_name'], 'agentview')
            return np.zeros((8,12,3), np.uint8), np.ones((8,12))
        def process(raw):
            self.assertEqual(low.render_camera_names, ['agentview'])
            self.assertEqual(set(raw), {'agentview_image', 'agentview_depth'})
            raw['agentview'] = dict(images=dict(rgb=raw['agentview_image'], depth=raw['agentview_depth']),
                                    intrinsics=np.eye(3), pose=np.array([0,0,0,1,0,0,0]))
        low._process_camera_observations = process
        low.robosuite_env = SimpleNamespace(sim=SimpleNamespace(render=render, data=SimpleNamespace(qpos=np.array([.01,.02]))),
            robots=[SimpleNamespace(_ref_gripper_joint_pos_indexes={'right': [0,1]})])
        return SimpleNamespace(low=low, encoder=[0.]*7,
            episode=SimpleNamespace(thread=SimpleNamespace(ident=threading.get_ident())))

    def test_public_reader_allowlist_calibration_and_restoration(self):
        backend = self.backend()
        packet = read_camera_packet(backend, 'agentview')
        self.assertEqual(packet['camera_name'], 'agentview')
        self.assertEqual(packet['coordinate_frame'], 'cap_robot_base')
        self.assertEqual(backend.low.render_camera_names, ['robot0_robotview'])
        self.assertEqual(set(packet), {'camera_name','coordinate_frame','length_unit','angle_unit','quaternion_order',
                                      'joint_positions','gripper_joint_positions','rgb','depth','intrinsics','camera_pose_xyz_wxyz'})
        with self.assertRaises(ValueError):
            read_camera_packet(backend, '../../truth')

    def test_wrong_owner_rejected_and_decoder_failure_restores_configuration(self):
        backend = self.backend()
        backend.episode.thread.ident = -1
        with self.assertRaises(RuntimeError):
            read_camera_packet(backend, 'agentview')
        backend.episode.thread.ident = threading.get_ident()
        def broken(raw): raise ValueError('sensor unavailable')
        backend.low._process_camera_observations = broken
        with self.assertRaises(ValueError):
            read_camera_packet(backend, 'agentview')
        self.assertEqual(backend.low.render_camera_names, ['robot0_robotview'])


class SupportViewSelectionTests(unittest.TestCase):
    def bundle(self):
        stamp = time.time()
        base = dict(episode_id='episode', control_step=120, captured_at=stamp,
            coordinate_convention=copy.deepcopy(COORDINATE_CONVENTION), evidence_ref='frame-000120.npz',
            objects={'green cube': {'status': 'unknown'}})
        view = dict(base, camera_name='agentview', evidence_ref='view-agentview-000120.npz',
            image_ref='view-agentview-000120.png', objects={'green cube': dict(status='visible',
                surface_span_xyz=[.04,.04,.04], surface_median_xyz=[.5,0,.04], valid_depth_pixels=100)})
        base['camera_views'] = {'agentview': view}
        return base

    def choose(self, bundle):
        return choose_support_view(bundle, 'green cube', None, _single_support_evidence)

    def test_current_alternative_supplies_evidence_without_relabeling_primary(self):
        bundle = self.bundle()
        evidence = self.choose(bundle)
        self.assertEqual(evidence['status'], 'available')
        self.assertEqual(evidence['camera_name'], 'agentview')
        self.assertEqual(evidence['primary_evidence']['status'], 'unknown')
        self.assertEqual(evidence['evidence_ref'], 'view-agentview-000120.npz')
        self.assertEqual(bundle['objects']['green cube']['status'], 'unknown')

    def test_all_unknown_and_truncated_views_remain_unknown_with_same_threshold(self):
        bundle = self.bundle()
        bundle['camera_views']['agentview']['objects']['green cube']['surface_span_xyz'] = [.001,.04,.04]
        self.assertEqual(self.choose(bundle)['status'], 'unknown')
        bundle['camera_views']['agentview']['objects']['green cube'] = {'status':'unknown'}
        self.assertEqual(self.choose(bundle)['status'], 'unknown')

    def test_stale_mixed_steps_episodes_and_uncalibrated_views_are_not_evidence(self):
        mutations = [lambda v: v.update(captured_at=time.time()-2),
            lambda v: v.update(control_step=119), lambda v: v.update(episode_id='previous-episode'),
            lambda v: v.update(coordinate_convention={'frame':'world'}),
            lambda v: v.update(camera_name='frontview'),
            lambda v: v.update(evidence_ref='view-agentview-000119.npz'),
            lambda v: v.update(image_ref='../../unrecorded.png')]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                bundle = self.bundle(); mutate(bundle['camera_views']['agentview'])
                self.assertEqual(self.choose(bundle)['status'], 'unknown')

    def test_fresh_view_cannot_rehabilitate_a_stale_primary_bundle(self):
        bundle = self.bundle(); bundle['captured_at'] -= 2
        self.assertEqual(self.choose(bundle)['status'], 'unknown')

    def test_available_primary_keeps_its_source_without_needing_other_camera(self):
        bundle = self.bundle()
        bundle['objects'] = copy.deepcopy(bundle['camera_views']['agentview']['objects'])
        evidence = self.choose(bundle)
        self.assertEqual(evidence['camera_name'], 'robot0_robotview')
        self.assertEqual(evidence['evidence_ref'], bundle['evidence_ref'])


class PublicSurfaceUnionTests(unittest.TestCase):
    def packets(self):
        def packet(name, pose, stem):
            rgb = np.zeros((20,20,3),dtype=np.uint8); rgb[:,:,1] = 200
            return dict(rgb=rgb, depth=np.ones((20,20)), intrinsics=np.array([[500.,0,10],[0,500.,10],[0,0,1]]),
                camera_pose_xyz_wxyz=np.array(pose), camera_name=name, episode_id='episode',
                coordinate_frame='cap_robot_base', length_unit='m', quaternion_order='WXYZ',
                control_step=120, captured_at=time.time()-.01, evidence_ref=stem+'.npz', image_ref=stem+'.png')
        s = np.sqrt(.5)
        primary = packet('robot0_robotview',[-.52,.02,.02,s,0,s,0],'frame-000120')
        wrist = packet('robot0_eye_in_hand',[.5,1.04,.02,s,s,0,0],'view-robot0_eye_in_hand-000120')
        reference = dict(objects={'green cube': dict(surface_span_xyz=[.04,.04,.04], surface_median_xyz=[100,100,100])})
        return primary, wrist, reference

    def test_complementary_measured_surfaces_pass_existing_threshold_without_old_position(self):
        a,b,ref = self.packets()
        fusion = fuse_public_surface(a,b,'green cube',ref)
        self.assertEqual(fusion['status'],'candidate')
        root, view = camera_snapshot(a), camera_snapshot(b)
        self.assertEqual(_single_support_evidence(root,'green cube',ref)['status'],'unknown')
        self.assertEqual(_single_support_evidence(view,'green cube',ref)['status'],'unknown')
        root.update(camera_views={'robot0_eye_in_hand':view}, support_view_fusions={'green cube':fusion})
        evidence = support_evidence(root,'green cube',ref)
        self.assertEqual(evidence['status'],'available')
        self.assertEqual(evidence['camera_name'],'public_rgbd_union')
        self.assertGreaterEqual(evidence['horizontal_span_ratio'],.4)
        self.assertEqual(evidence['evidence_refs'],[a['evidence_ref'],b['evidence_ref']])
        self.assertLess(evidence['support_geometry']['surface_median_xyz'][0],1)
        root['camera_views']['robot0_eye_in_hand']['control_step'] -= 1
        self.assertEqual(support_evidence(root,'green cube',ref)['status'],'unknown')

    def test_missing_pixels_stale_packets_calibration_and_oversized_union_fail_closed(self):
        cases = [lambda b:b.update(control_step=119), lambda b:b.update(episode_id='other'),
            lambda b:b.update(captured_at=time.time()-2), lambda b:b.update(captured_at=time.time()+1),
            lambda b:b['rgb'].fill(0), lambda b:b['depth'].fill(np.nan),
            lambda b:b.update(coordinate_frame='world'), lambda b:b['intrinsics'].fill(0),
            lambda b:b['camera_pose_xyz_wxyz'].__setitem__(0,1.0)]
        for mutate in cases:
            a,b,ref=self.packets(); mutate(b)
            self.assertEqual(fuse_public_surface(a,b,'green cube',ref)['status'],'unknown')
        a,b,ref=self.packets()
        self.assertEqual(fuse_public_surface(a,b,'green cube',None)['status'],'unknown')

    def test_actual_release_guard_requires_current_union_and_never_treats_visibility_as_finish(self):
        from agentic_cap.active_evidence import install_inspector
        from agentic_cap.feedback import EffectError
        a,b,ref = self.packets()
        root, view = camera_snapshot(a), camera_snapshot(b)
        fusion = fuse_public_surface(a,b,'green cube',ref)
        root.update(camera_views={'robot0_eye_in_hand':view},support_view_fusions={'green cube':fusion})
        events, opens = [], []
        def emit(kind, **values):
            row=dict(kind=kind,id=len(events)+1,**values); events.append(row); return row
        e=SimpleNamespace(goal={'on':['red cube','green cube'],'empty_gripper':True},gripper=0,
            checkpoint=lambda segment:None,observe=lambda:root,event=emit,events=events,
            used={'calls':0},limits={'calls':40},lock=threading.RLock())
        backend=SimpleNamespace(episode=e,reference=ref,held_hint='red cube',holding_results={'red cube':'verified'},tick=120)
        tools=install_inspector(backend,{'id':'segment','revision':1},{'open_gripper':lambda:opens.append(True)})
        root['camera_views']['robot0_eye_in_hand']['control_step']=119
        with self.assertRaises(EffectError):tools['open_gripper']()
        self.assertEqual(opens,[])
        root['camera_views']['robot0_eye_in_hand']['control_step']=120
        tools['open_gripper']()
        self.assertEqual(opens,[True])
        self.assertEqual(events[-2]['kind'],'placement_release_checked')
        self.assertFalse(any(v['kind']=='finish_checked' for v in events))
