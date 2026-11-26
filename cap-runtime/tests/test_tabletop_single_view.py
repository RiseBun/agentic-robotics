"""Fallback must measure current pixels; never turn dropout into inferred success."""
import unittest
import numpy as np
import test_tabletop_multiview as fixtures
from agentic_cap.tabletop_multiview import measure_single_view

class SingleViewTests(unittest.TestCase):
    def data(self):return fixtures.MultiviewEvidenceTests().data()
    def test_overview_is_preferred_without_union(self):
        p,ids=self.data();r=measure_single_view(p,'item',ids)
        self.assertEqual(r['status'],'available');self.assertEqual(r['camera'],'overview')
        self.assertEqual(r['contributing_cameras'],['overview'])
    def test_empty_overview_mask_still_tries_wrist(self):
        p,ids=self.data();p['cameras']['overview']['rgb'][:]=0
        r=measure_single_view(p,'item',ids,initial_mask=np.zeros((40,40),bool))
        self.assertEqual(r['status'],'available');self.assertEqual(r['camera'],'wrist')
    def test_two_dropouts_do_not_invent_geometry(self):
        p,ids=self.data()
        for c in p['cameras'].values():c['rgb'][:]=0;c['depth'][:]=np.nan
        self.assertEqual(measure_single_view(p,'item',ids)['status'],'unknown')
    def test_stale_or_miscalibrated_wrist_cannot_replace_missing_overview(self):
        for broken in ('time','calibration'):
            p,ids=self.data();p['cameras']['overview']['rgb'][:]=0
            if broken=='time':p['cameras']['wrist']['captured_at']-=.2
            else:p['cameras']['wrist']['camera_to_world'][0,0]=2
            self.assertEqual(measure_single_view(p,'item',ids)['status'],'unknown')
if __name__=='__main__':unittest.main()
