"""Projection ranks finite candidates only; never supplies verified facts."""
import copy,time,unittest
import numpy as np
from agentic_cap.tabletop_views import rank_views,signature

class ViewTests(unittest.TestCase):
    def packet(self):
        T=np.eye(4);T[:3,3]=[0,0,1.];T[:3,:3]=np.diag([1.,-1.,-1.])
        return dict(captured_at=time.time(),tcp_xyz=[0,0,1.],cameras={'wrist':dict(depth=np.ones((240,320)),
            captured_at=time.time(),intrinsics=np.array([[200.,0,160],[0,200.,120],[0,0,1]]),camera_to_world=T)})
    def test_candidates_are_bounded_and_keep_rotation_out_of_interface(self):
        rows=rank_views(self.packet(),[0,0,1],[0,0,.83],[.13,.13,.03])
        self.assertTrue(rows)
        for row in rows:
            x,y,z=row['target_xyz'];self.assertLessEqual(abs(x),.08);self.assertLessEqual(abs(y),.08)
            self.assertIn(z,[1.,1.06]);self.assertNotIn('verified',row);self.assertNotIn('target_wxyz',row)
    def test_missing_depth_changes_which_view_is_ranked_first(self):
        p=self.packet();a=rank_views(p,[0,0,1],[0,0,.83],[.08,.08,.03])
        p['cameras']['wrist']['depth'][:,160:]=np.nan
        b=rank_views(p,[0,0,1],[0,0,.83],[.08,.08,.03])
        self.assertNotEqual(a[0]['target_xyz'],b[0]['target_xyz'])
        self.assertGreater(b[0]['predicted_valid_fraction'],b[-1]['predicted_valid_fraction'])
    def test_visited_pose_uses_centimeter_cells_not_encoder_drift(self):
        p=self.packet();a=rank_views(p,[0,0,1],[0,0,.83],[.08,.08,.03]);chosen=a[0]
        visited={tuple(chosen['signature'])}
        p['tcp_xyz']=[.0002,0,1.0003]
        rows=rank_views(p,[.0002,0,1.0003],[0,0,.83],[.08,.08,.03],visited)
        self.assertTrue(all(tuple(r['signature']) not in visited for r in rows))
    def test_held_candidate_keeps_x_z(self):
        rows=rank_views(self.packet(),[0,0,1],[0,0,.83],[.08,.08,.03],holding=True)
        for row in rows:self.assertEqual(row['target_xyz'][0],0);self.assertEqual(row['target_xyz'][2],1)
        self.assertLessEqual(len(rows),2)
    def test_stale_miscalibrated_and_unknown_target_do_not_make_candidates(self):
        for problem in ['stale','rotation','future','point']:
            p=self.packet();center=[0,0,.83]
            if problem=='stale':p['captured_at']-=2;p['cameras']['wrist']['captured_at']-=2
            if problem=='future':p['captured_at']+=2;p['cameras']['wrist']['captured_at']+=2
            if problem=='rotation':p['cameras']['wrist']['camera_to_world'][0,0]=2
            if problem=='point':center[0]=float('nan')
            self.assertEqual(rank_views(p,[0,0,1],center,[.08,.08,.03]),[])
    def test_current_camera_translation_is_used_when_anchor_is_frozen(self):
        p=self.packet();a=rank_views(p,[0,0,1],[0,0,.83],[.08,.08,.03])
        p['tcp_xyz']=[.02,0,1.];p['cameras']['wrist']['camera_to_world'][0,3]=.02
        b=rank_views(p,[0,0,1],[0,0,.83],[.08,.08,.03])
        self.assertEqual([r['target_xyz'] for r in a],[r['target_xyz'] for r in b])
if __name__=='__main__':unittest.main()
