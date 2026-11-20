import unittest
from score_campaign import assess


class ScoringTests(unittest.TestCase):
    def row(self):
        return dict(outcome='completed', terminal_at=10, ended_at=12)

    def samples(self, values):
        return [dict(time=10+i*.6, task_completed=value, revision=1) for i, value in enumerate(values)]

    def events(self, extra=None):
        return [*(extra or []), dict(kind='finish_checked', time=10, result={'status': 'verified'}), dict(kind='episode_closed', time=12)]

    def test_missing_truth_never_confirms_public_completion(self):
        result = assess(self.row(), [], [])
        self.assertIsNone(result['independent_success'])
        self.assertIsNone(result['false_completion'])

    def test_brief_or_mixed_success_cannot_pass_the_settle_window(self):
        self.assertIsNone(assess(self.row(), self.samples([True]), self.events())['independent_success'])
        result = assess(self.row(), self.samples([True, False]), self.events())
        self.assertFalse(result['independent_success'])
        self.assertTrue(result['false_completion'])

    def test_only_observed_failure_counts_as_a_recovery_opportunity(self):
        unknown = dict(kind='api_error', time=9, feedback=dict(category='insufficient_evidence'))
        failed = dict(kind='api_error', time=9, feedback=dict(category='motion_target_not_reached'))
        self.assertFalse(assess(self.row(), self.samples([True, True]), self.events([unknown]))['failure_observed'])
        self.assertTrue(assess(self.row(), self.samples([True, True]), self.events([failed]))['recovered_after_observed_failure'])

    def test_workbench_clock_offset_cannot_change_physical_score(self):
        row = self.row()
        row['terminal_at'] += 3600
        row['ended_at'] += 3600
        self.assertTrue(assess(row, self.samples([True, True]), self.events())['independent_success'])

    def test_legacy_unknown_api_exception_is_not_a_recovered_failure(self):
        old = [dict(kind='api_started', time=8, name='check_holding', segment_id='s'),
               dict(kind='api_result', time=9, name='check_holding', segment_id='s', result={'status': 'unknown'}),
               dict(kind='api_error', time=9, name='check_holding', segment_id='s', error='API effect unknown')]
        result = assess(self.row(), self.samples([True, True]), self.events(old))
        self.assertFalse(result['failure_observed'])
        self.assertFalse(result['recovered_after_observed_failure'])

    def test_physical_completion_is_independent_of_the_models_declaration(self):
        row = dict(outcome='needs_attention', terminal_at=10, ended_at=12)
        events = [dict(kind='episode_closed', time=11.2)]
        result = assess(row, self.samples([True, True]), events)
        self.assertTrue(result['independent_success'])
        self.assertFalse(result['verified_system_success'])
        self.assertTrue(result['physical_success_unacknowledged'])
