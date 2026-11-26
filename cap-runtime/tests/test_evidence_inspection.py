"""Counterexamples for evidence ordering; prediction never changes verification."""
import copy,time,unittest
import numpy as np
from agentic_cap.tabletop_inspection import evidence_request,order_evidence_views
from agentic_cap.tabletop_views import rank_views
from agentic_cap.tabletop_sensors import support_surface,support_extent_assessment
import test_tabletop_views as views
import test_inspection_coverage_actions as actions
from agentic_cap.tabletop_actions import tools
from agentic_cap.core import Halt

class EvidenceInspectionTests(unittest.TestCase):
    def data(self):
        packet=views.ViewTests().packet();at=packet['captured_at']
        ref={'platform':dict(surface_median_xyz=[0,0,.83],surface_span_xyz=[.08,.08,.03])}
        snapshot=dict(objects={'block_a':dict(status='available',surface_median_xyz=[.03,0,.94],surface_span_xyz=[.04,.04,.04],captured_at=at)})
        req=evidence_request('platform','support_geometry',{'status':'unknown'},'block_a',{'status':'verified'})
        candidates=rank_views(packet,[0,0,1],[0,0,.83],[.08,.08,.03],holding=True)
        return candidates,packet,snapshot,ref,req
    def test_reordering_never_adds_candidates_or_mutates_input(self):
        c,p,s,r,q=self.data();before=copy.deepcopy(c)
        result=order_evidence_views(c,p,s,r,q,'block_a')
        self.assertEqual(c,before);self.assertEqual(sorted(x['signature'] for x in c),sorted(x['signature'] for x in result))
        self.assertTrue(all(x['evidence_prediction']['status']=='predicted' for x in result))
        self.assertTrue(all('verified' not in x['evidence_prediction'] for x in result))
    def test_gap_preserves_missing_axis_and_explicit_joint_requirement(self):
        result={'conditions':[{'condition':{'predicate':'support_geometry','object':'platform'},'status':'unknown',
            'reason':'support_extent_not_established_from_current_surface','evidence_gap':{'missing_axes':[1],'reason':'missing_extent_requires_current_planar_side'}}]}
        q=evidence_request('platform','support_geometry',result,'block_a',{'status':'verified'})
        self.assertEqual(q['missing_axes'],[1]);self.assertEqual(len(q['requirements']),2)
        c,p,s,r,_=self.data();ranked=order_evidence_views(c,p,s,r,q,'block_a')
        self.assertEqual(ranked[0]['evidence_prediction']['required_axes'],[1])
        self.assertEqual(len(ranked[0]['evidence_prediction']['target_edge_fractions']),2)
    def test_more_area_cannot_compensate_for_a_missing_required_boundary(self):
        c,p,s,r,q=self.data();q['missing_axes']=[1]
        c=rank_views(p,[0,0,1],[0,0,.83],[.08,.08,.03],holding=False)
        # A fixed image dropout erases one candidate's required boundary.
        p['cameras']['wrist']['depth'][:90,:]=np.nan
        ranked=order_evidence_views(c,p,s,r,q)
        scores=[x['evidence_prediction']['joint_minimum_fraction'] for x in ranked]
        self.assertGreater(scores[0],scores[-1])
    def test_missing_custody_does_not_invent_a_joint_prediction(self):
        c,p,s,r,q=self.data();q['custody']={'status':'unknown'}
        ranked=order_evidence_views(c,p,s,r,q,'block_a')
        self.assertEqual([x['signature'] for x in c],[x['signature'] for x in ranked])
        self.assertEqual(ranked[0]['evidence_prediction']['reason'],'current_custody_geometry_unavailable')
    def test_stale_custody_and_mismatched_source_keep_explicit_fallback(self):
        for problem in ['stale','wrong_source','future']:
            c,p,s,r,q=self.data()
            if problem=='stale':s['objects']['block_a']['captured_at']-=2
            elif problem=='future':s['objects']['block_a']['captured_at']+=2
            else:s.update(geometry_evidence_schema=1,episode_id='x',control_step=1,captured_at=p['captured_at'])
            result=order_evidence_views(c,p,s,r,q,'block_a')
            self.assertTrue(all(x['evidence_prediction']['status']=='unavailable' for x in result))
    def test_current_custody_projection_can_change_joint_preference(self):
        c,p,s,r,q=self.data()
        # The wrist aperture loses the held surface. A fixed camera preserves
        # custody for only one proposed translation; target wrist edges remain.
        p['cameras']['overview']=copy.deepcopy(p['cameras']['wrist'])
        p['cameras']['overview']['depth'][:]=np.nan
        p['cameras']['overview']['depth'][160:,:]=1.
        # Move held surface out of wrist FOV while keeping it in overview FOV.
        p['cameras']['overview']['camera_to_world'][2,3]=1.2
        r['platform']['surface_span_xyz']=[.03,.03,.03]
        ranked=order_evidence_views(c,p,s,r,q,'block_a')
        self.assertTrue(all('custody_aperture_fraction' in x['evidence_prediction'] for x in ranked))
        self.assertTrue(all(0<=x['evidence_prediction']['joint_minimum_fraction']<=1 for x in ranked))
        self.assertGreater(ranked[0]['evidence_prediction']['custody_aperture_fraction'],ranked[-1]['evidence_prediction']['custody_aperture_fraction'])
        self.assertGreater(ranked[0]['evidence_prediction']['joint_minimum_fraction'],ranked[-1]['evidence_prediction']['joint_minimum_fraction'])
    def test_stale_camera_or_bad_rotation_never_produces_a_prediction(self):
        for bad in ['stale','rotation']:
            c,p,s,r,q=self.data()
            if bad=='stale':p['captured_at']-=2
            else:p['cameras']['wrist']['camera_to_world'][0,0]=2
            self.assertTrue(all(x['evidence_prediction']['status']=='unavailable' for x in order_evidence_views(c,p,s,r,q)))
    def test_top_patch_explains_missing_y_without_relaxing_verifier(self):
        current=dict(surface_q02_xyz=[0,0,.84],surface_q98_xyz=[.1,.03,.8401],surface_median_xyz=[.05,.015,.84],plane_normal_xyz=[0,0,1],plane_residual_m=.0001,camera_center_world=[0,0,1])
        ref=dict(surface_span_xyz=[.1,.1,.04],observed_height_m=.04)
        d=support_extent_assessment(current,ref)
        self.assertEqual(d['missing_axes'],[1]);self.assertAlmostEqual(d['span_fraction_xy'][1],.3)
        self.assertEqual(d['reason'],'missing_extent_requires_current_planar_side');self.assertIsNone(support_surface(current,ref))
    def test_actual_planar_side_still_reconstructs_under_original_rules(self):
        c=dict(surface_q02_xyz=[.02,-.02,.8],surface_q98_xyz=[.021,.02,.82],surface_median_xyz=[.02,0,.81],plane_normal_xyz=[1,0,0],plane_residual_m=.0005,camera_center_world=[1,0,1.2])
        r=dict(surface_span_xyz=[.04,.04,.04],observed_height_m=.04)
        self.assertEqual(support_extent_assessment(c,r)['status'],'available')
        self.assertAlmostEqual(support_surface(c,r)['surface_q02_xyz'][0],-.02)
    def backend(self):
        b,segment=actions.CoverageActionTests().backend();b.inspection_policy='evidence';return b,segment
    def test_evidence_policy_keeps_two_attempt_limit_and_records_request(self):
        b,segment=self.backend();result=tools(b,segment)['inspect_object']('platform','support_geometry')
        self.assertEqual(result['views_used'],2);self.assertEqual(b._move_trajectory.call_count,2)
        self.assertTrue(any(c.args[0]=='inspection_evidence_request' for c in b.episode.event.call_args_list))
    def test_unknown_custody_still_prevents_physical_dispatch(self):
        b,segment=self.backend();b.held_hint='block_a'
        result=tools(b,segment)['inspect_object']('platform','support_geometry')
        self.assertEqual(result['views_used'],0);b._move_trajectory.assert_not_called()
    def test_cancellation_still_prevents_dispatch(self):
        b,segment=self.backend();b.episode.checkpoint.side_effect=Halt('stop','CANCELLED')
        with self.assertRaises(Halt):tools(b,segment)['inspect_object']('platform','support_geometry')
        b._move_trajectory.assert_not_called()
if __name__=='__main__':unittest.main()
