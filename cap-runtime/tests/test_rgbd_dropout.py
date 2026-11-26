"""Sensor fault injection changes actual pixels/depth, never verifier outcomes."""
import copy,unittest
import numpy as np
from experiments.rgbd_occlusion_service import apply_dropout,RECTANGLES
from agentic_cap.tabletop_backend import TabletopBackend

class DropoutTests(unittest.TestCase):
    def data(self):
        return dict(control_step=20,captured_at=100.,tcp_xyz=[0,0,1],cameras={k:dict(rgb=np.full((240,320,3),120,np.uint8),
            depth=np.ones((240,320)),intrinsics=np.eye(3),camera_to_world=np.eye(4),captured_at=99.99) for k in RECTANGLES})
    def test_fixed_dropout_changes_only_declared_pixels_and_depth(self):
        p=self.data();before=copy.deepcopy(p);apply_dropout(p,'recoverable')
        for name,camera in p['cameras'].items():
            x0,y0,x1,y1=RECTANGLES[name];mask=np.zeros((240,320),bool);mask[y0:y1,x0:x1]=True
            self.assertTrue(np.isnan(camera['depth'][mask]).all());self.assertTrue((camera['rgb'][mask]==0).all())
            np.testing.assert_equal(camera['depth'][~mask],before['cameras'][name]['depth'][~mask])
            self.assertEqual(camera['captured_at'],99.99);np.testing.assert_equal(camera['camera_to_world'],np.eye(4))
        self.assertEqual(p['tcp_xyz'],before['tcp_xyz']);self.assertEqual(p['control_step'],20)
    def test_persistent_dropout_removes_all_wrist_depth(self):
        p=self.data();apply_dropout(p,'persistent');self.assertTrue(np.isnan(p['cameras']['wrist']['depth']).all())
        self.assertTrue(np.isfinite(p['cameras']['overview']['depth'][:100]).all())
    def test_production_camera_adapter_is_identity(self):
        b=object.__new__(TabletopBackend);packet=self.data()
        self.assertIs(b.transform_camera_packet(packet),packet)
    def test_provider_depth_sanitizes_missing_pixels_without_inventing_points(self):
        from agentic_cap.tabletop_sensors import provider_depth,project
        original=np.array([[np.nan,np.inf,-1.],[0.,1.,2.]])
        finite=provider_depth(original)
        self.assertTrue(np.isfinite(finite).all());self.assertTrue(np.isnan(original[0,0]))
        np.testing.assert_equal(finite,[[0,0,0],[0,1,2]])
        camera=dict(depth=finite,rgb=np.zeros((2,3,3),np.uint8),intrinsics=np.eye(3),camera_to_world=np.eye(4))
        points,_=project(camera)
        self.assertEqual(len(points),2)

if __name__=='__main__':unittest.main()
