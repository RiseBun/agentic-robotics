"""Fixed identity uses historical measured region, never historical current truth."""
import copy,unittest
import numpy as np
import test_tabletop_multiview as fixtures
from agentic_cap.tabletop_multiview import measure_single_view,fixed_reference_mask

class FixedSupportTests(unittest.TestCase):
    def data(self):
        p,ids=fixtures.MultiviewEvidenceTests().data()
        # These are public synthetic measured bounds, not simulator labels.
        ref=dict(surface_q02_xyz=[-.14,-.18,.85],surface_q98_xyz=[-.10,-.14,.86],
                 surface_span_xyz=[.04,.04,.01],surface_median_xyz=[-.12,-.16,.85])
        return p,ids,ref
    def test_current_pixels_inside_measured_fixed_region_are_usable(self):
        p,ids,ref=self.data();r=measure_single_view(p,'item',ids,movable=False,reference=ref)
        self.assertEqual(r['status'],'available')
    def test_remote_same_color_robot_surface_cannot_replace_fixed_support(self):
        p,ids,ref=self.data()
        for c in p['cameras'].values():c['camera_to_world'][2,3]+=.25
        r=measure_single_view(p,'item',ids,movable=False,reference=ref)
        self.assertEqual(r['status'],'unknown')
    def test_no_pixels_in_reference_region_remains_unknown(self):
        p,ids,ref=self.data()
        for c in p['cameras'].values():c['depth'][:]=np.nan
        self.assertEqual(measure_single_view(p,'item',ids,movable=False,reference=ref)['status'],'unknown')
    def test_movable_object_is_not_pinned_to_initial_region(self):
        p,ids,ref=self.data()
        for c in p['cameras'].values():c['camera_to_world'][2,3]+=.25
        self.assertEqual(measure_single_view(p,'item',ids,movable=True,reference=ref)['status'],'available')
    def test_region_filter_precedes_largest_component_selection(self):
        from agentic_cap.tabletop_multiview import track_mask
        p,ids,ref=self.data();cam=p['cameras']['overview']
        cam['rgb'][:]=0;cam['rgb'][4:12,4:12]=[240,30,30];cam['rgb'][18:38,18:38]=[240,30,30]
        cam['depth'][18:38,18:38]=1.25
        mask=track_mask(cam,'item',ids,None,False,ref)
        self.assertTrue(mask[5,5]);self.assertFalse(mask[20,20])

if __name__=='__main__':unittest.main()
