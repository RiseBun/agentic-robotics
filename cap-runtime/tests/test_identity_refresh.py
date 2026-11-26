"""A failed semantic refresh must not fabricate or erase RGB-D evidence."""
import copy,time,unittest
from types import SimpleNamespace
from unittest.mock import Mock
import numpy as np
import test_tabletop_multiview as fixture
from agentic_cap.tabletop_multiview import refresh_identity,measure_single_view

class RefreshTests(unittest.TestCase):
    def data(self):
        p,ids=fixture.MultiviewEvidenceTests().data()
        ids['item']['score']=.9
        return p,ids
    def result(self,mask,ok=True):return SimpleNamespace(ok=ok,segmentations=[SimpleNamespace(mask=mask,score=.8)] if mask is not None else [])
    def test_missing_semantics_retains_original_identity_time_and_current_wrist_path(self):
        p,ids=self.data();original=copy.deepcopy(ids['item']);p['cameras']['overview']['rgb'][:]=0
        self.assertIsNone(refresh_identity(p['cameras']['overview'],'item',ids,self.result(None)))
        np.testing.assert_equal(ids['item']['chroma'],original['chroma']);self.assertEqual(ids['item']['identity_observed_at'],original['identity_observed_at'])
        r=measure_single_view(p,'item',ids)
        self.assertEqual(r['status'],'available');self.assertEqual(r['camera'],'wrist')
    def test_retained_identity_without_current_pixels_remains_unknown(self):
        p,ids=self.data()
        for cam in p['cameras'].values():cam['rgb'][:]=0
        refresh_identity(p['cameras']['overview'],'item',ids,self.result(None))
        self.assertEqual(measure_single_view(p,'item',ids)['status'],'unknown')
    def test_unknown_identity_is_not_created_on_semantic_failure(self):
        p,_=self.data();ids={}
        self.assertIsNone(refresh_identity(p['cameras']['overview'],'item',ids,self.result(None)));self.assertEqual(ids,{})
    def test_mask_over_invalid_or_black_depth_cannot_replace_identity(self):
        for broken in ['depth','rgb']:
            p,ids=self.data();cam=p['cameras']['overview'];old=copy.deepcopy(ids['item'])
            if broken=='depth':cam['depth'][:]=np.nan
            else:cam['rgb'][:]=0
            self.assertIsNone(refresh_identity(cam,'item',ids,self.result(np.ones((40,40),bool))))
            self.assertEqual(ids['item']['identity_observed_at'],old['identity_observed_at'])
            np.testing.assert_equal(ids['item']['chroma'],old['chroma'])
    def test_success_commits_new_identity_and_only_valid_mask(self):
        p,ids=self.data();cam=p['cameras']['overview'];cam['captured_at']+=.5
        mask=np.zeros((40,40),bool);mask[12:28,12:28]=True;cam['depth'][12,12]=np.nan
        r=refresh_identity(cam,'item',ids,self.result(mask))
        self.assertFalse(r[12,12]);self.assertEqual(int(r.sum()),255)
        self.assertEqual(ids['item']['identity_observed_at'],cam['captured_at'])
    def test_retained_ambiguous_identity_cannot_assign_other_object(self):
        p,ids=self.data();ids['other']=copy.deepcopy(ids['item']);p['cameras']['overview']['rgb'][:]=0
        refresh_identity(p['cameras']['overview'],'item',ids,self.result(None))
        self.assertEqual(measure_single_view(p,'item',ids)['status'],'unknown')
    def test_semantic_success_on_different_appearance_cannot_overwrite_identity(self):
        p,ids=self.data();cam=p['cameras']['overview'];old=copy.deepcopy(ids['item']);cam['rgb'][:]=[100,100,100]
        self.assertIsNone(refresh_identity(cam,'item',ids,self.result(np.ones((40,40),bool))))
        np.testing.assert_equal(ids['item']['chroma'],old['chroma'])
        self.assertEqual(ids['item']['identity_observed_at'],old['identity_observed_at'])
    def test_new_identity_matching_existing_object_remains_ambiguous(self):
        p,ids=self.data();cam=p['cameras']['overview'];mask=np.zeros((40,40),bool);mask[12:28,12:28]=True
        self.assertIsNone(refresh_identity(cam,'new_item',ids,self.result(mask)))
        self.assertNotIn('new_item',ids)

if __name__=='__main__':unittest.main()
