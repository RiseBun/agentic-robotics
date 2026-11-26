"""Public sensor readiness metadata, independent of task truth."""
import unittest
import test_runtime as fixtures


class DecisionEvidenceTests(unittest.TestCase):
    setUp = fixtures.RuntimeTests.setUp
    tearDown = fixtures.RuntimeTests.tearDown

    def test_backend_without_temporal_window_reports_not_required(self):
        self.assertEqual(self.e.observe()['decision_evidence']['status'], 'not_required')

    def test_ready_does_not_establish_goal_when_occluded(self):
        self.e.backend.verification_readiness=lambda goals,snapshot:dict(status='ready',evidence_ref=snapshot.get('evidence_ref'))
        self.e.backend.occluded=True
        result=self.e.observe()
        self.assertEqual(result['decision_evidence']['status'],'ready')
        self.assertEqual(result['system']['world']['facts']['goal']['status'],'unknown')
        self.assertEqual(self.e.used['calls'],0)
        self.assertEqual(self.e.used['decisions'],0)

    def test_readiness_covers_both_holding_and_placement_windows(self):
        seen=[]
        def readiness(goals,snapshot):
            seen.extend(goals)
            return dict(status='pending',reason='incomplete_window')
        self.e.backend.verification_readiness=readiness
        result=self.e.observe()
        self.assertEqual(result['decision_evidence']['status'],'pending')
        self.assertTrue(any('holding' in g for g in seen))
        self.assertTrue(any('on' in g for g in seen))

    def test_broken_or_malformed_readiness_stays_unknown(self):
        def broken(*_):raise RuntimeError('sensor unavailable')
        for implementation in [broken,lambda *_:dict(status='verified'),lambda *_:None]:
            self.e.backend.verification_readiness=implementation
            self.assertEqual(self.e.observe()['decision_evidence']['status'],'unknown')
