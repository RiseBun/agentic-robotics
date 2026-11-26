"""View execution waits for evidence, not elapsed physics ticks, before next motion."""
import unittest
from unittest.mock import Mock
import test_inspection_coverage_actions as fixtures
from agentic_cap.tabletop_actions import tools
from agentic_cap.core import Halt

class JointInspectionTests(unittest.TestCase):
    def backend(self):
        b,s=fixtures.CoverageActionTests().backend();b.held_hint='block_a';b.resolved=False
        def verify(goal,_snapshot):
            p=goal['all'][0]['predicate']
            if p=='holding':return {'status':'verified'}
            if p=='empty_gripper':return {'status':'failed'}
            return {'status':'verified' if b.resolved else 'unknown'}
        b.verify.side_effect=verify
        return b,s
    def test_waits_for_joint_measurements_and_stops_on_first_verified_view(self):
        b,s=self.backend()
        def wait(segment,goal):
            self.assertEqual(goal,{'all':[{'predicate':'support_geometry','object':'platform'},{'predicate':'holding','object':'block_a'}]})
            b.resolved=True;return {'status':'verified'}
        b.wait_public_effect=Mock(side_effect=wait)
        result=tools(b,s)['inspect_object']('platform','support_geometry')
        self.assertEqual(result['status'],'available');self.assertEqual(result['views_used'],1)
        self.assertEqual(b._move_trajectory.call_count,1);b.wait_public_effect.assert_called_once()
        events=[c for c in b.episode.event.call_args_list if c.args[0]=='inspection_post_view_evidence']
        self.assertEqual(events[0].kwargs['custody']['status'],'verified')
    def test_unknown_custody_after_wait_prevents_second_view(self):
        b,s=self.backend();b.custody_lost=False;original=b.verify.side_effect
        b.verify.side_effect=lambda g,state:({'status':'unknown'} if b.custody_lost and g['all'][0]['predicate']=='holding' else original(g,state))
        def wait(*_):b.custody_lost=True;return {'status':'unknown'}
        b.wait_public_effect=Mock(side_effect=wait)
        result=tools(b,s)['inspect_object']('platform','support_geometry')
        self.assertEqual(result['status'],'unknown');self.assertEqual(result['views_used'],1)
        self.assertEqual(b._move_trajectory.call_count,1)
    def test_stop_during_wait_never_dispatches_next_candidate(self):
        b,s=self.backend();b.wait_public_effect=Mock(side_effect=Halt('cancel','CANCELLED'))
        with self.assertRaises(Halt):tools(b,s)['inspect_object']('platform','support_geometry')
        self.assertEqual(b._move_trajectory.call_count,1)
    def test_persistent_missing_target_with_known_custody_uses_at_most_two_views(self):
        b,s=self.backend();b.wait_public_effect=Mock(return_value={'status':'unknown'})
        result=tools(b,s)['inspect_object']('platform','support_geometry')
        self.assertEqual(result['status'],'unknown');self.assertEqual(result['views_used'],2)
        self.assertEqual(b.wait_public_effect.call_count,2)
if __name__=='__main__':unittest.main()
