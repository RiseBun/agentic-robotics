"""Calibrated synthetic RGB-D evidence tests; not robot performance results."""
import copy
import time
import unittest
import numpy as np
from unittest.mock import patch
from agentic_cap.tabletop_multiview import measure_object


class MultiviewEvidenceTests(unittest.TestCase):
    def data(self):
        now=time.time();identity={'item':dict(chroma=np.array([.8,.1,.1]),identity_observed_at=now)}
        def camera(x):
            rgb=np.zeros((40,40,3),dtype=np.uint8);rgb[12:28,12:28]=[240,30,30]
            depth=np.ones((40,40));K=np.array([[400.,0,20],[0,400.,20],[0,0,1]])
            T=np.eye(4);T[:3,3]=[x,-.16,-.15]
            return dict(rgb=rgb,depth=depth,intrinsics=K,camera_to_world=T,captured_at=now)
        packet=dict(captured_at=now,cameras=dict(overview=camera(-.12),wrist=camera(-.10)))
        return packet,identity
    def test_current_surfaces_from_both_cameras_contribute_world_bounds(self):
        packet,identities=self.data();result=measure_object(packet,'item',identities)
        self.assertEqual(result['status'],'available');self.assertEqual(result['contributing_cameras'],['overview','wrist'])
        self.assertAlmostEqual(result['surface_min_xyz'][0],-.14)
        self.assertAlmostEqual(result['surface_max_xyz'][0],-.0825)
        self.assertEqual(result['camera'],'overview')
        self.assertGreater(result['surface_span_xyz'][0],.05)
    def test_stale_camera_cannot_be_refreshed_by_a_new_packet_timestamp(self):
        packet,identities=self.data();packet['cameras']['wrist']['captured_at']-=.2
        result=measure_object(packet,'item',identities)
        self.assertEqual(result['status'],'unknown');self.assertIn('time',result['reason'])
    def test_invalid_calibration_is_unknown(self):
        packet,identities=self.data();packet['cameras']['wrist']['camera_to_world'][0,0]=2
        self.assertEqual(measure_object(packet,'item',identities)['status'],'unknown')
    def test_identity_geometry_disagreement_does_not_produce_a_larger_box(self):
        packet,identities=self.data();packet['cameras']['wrist']['camera_to_world'][0,3]=.10
        result=measure_object(packet,'item',identities)
        self.assertEqual(result['status'],'unknown');self.assertIn('inconsistent',result['reason'])
    def test_an_occluded_camera_can_be_replaced_by_a_current_usable_view(self):
        packet,identities=self.data();packet['cameras']['overview']['rgb'][:]=0
        result=measure_object(packet,'item',identities)
        self.assertEqual(result['status'],'available');self.assertEqual(result['contributing_cameras'],['wrist'])
    def test_no_visible_object_remains_unknown(self):
        packet,identities=self.data()
        for camera in packet['cameras'].values():camera['rgb'][:]=0
        self.assertEqual(measure_object(packet,'item',identities)['status'],'unknown')
    def test_ambiguous_appearance_is_not_assigned_to_both_objects(self):
        packet,identities=self.data();identities['duplicate']=copy.deepcopy(identities['item'])
        self.assertEqual(measure_object(packet,'item',identities)['status'],'unknown')
    def test_image_boundary_truncation_is_not_full_geometry(self):
        packet,identities=self.data()
        for camera in packet['cameras'].values():camera['rgb'][:]=[240,30,30]
        self.assertEqual(measure_object(packet,'item',identities)['status'],'unknown')

if __name__=='__main__':unittest.main()
