import json
import tempfile
import unittest
from pathlib import Path

from experiments.score_unified_system import score


class SystemScoringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.write(self.root/'protocol.json', dict(tasks=['a', 'b'], seed=12, settle_seconds=1.5, scope='fixture'))
        self.rows = [dict(task=name, episode_id=name, public_final=dict(status='verified'),
                          system=dict(world=dict(facts=dict(goal=dict(status='verified'))))) for name in ['a', 'b']]
        self.write(self.root/'results.json', self.rows)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, path, value):
        path.write_text(json.dumps(value))

    def episode(self, name, closed):
        folder = self.root/name
        folder.mkdir()
        self.write(folder/'episode.json', dict(status='closed' if closed else 'ready', segments={}, used=dict(calls=0)))
        (folder/'events.jsonl').write_text(json.dumps(dict(kind='episode_closed' if closed else 'episode_ready', time=10)))

    def test_missing_frozen_case_rejects_before_any_episode_or_private_file(self):
        self.write(self.root/'results.json', self.rows[:1])
        with self.assertRaisesRegex(RuntimeError, 'Incomplete'):
            score(self.root)

    def test_all_cases_must_close_before_first_private_file_can_be_read(self):
        self.episode('a', True)
        self.episode('b', False)
        # Neither private file exists. Reading A before checking B would fail
        # with FileNotFoundError, rather than the required closure refusal.
        with self.assertRaisesRegex(RuntimeError, 'still open'):
            score(self.root)

    def test_closed_case_scoring_keeps_false_confirmation_and_missing_truth_distinct(self):
        for name in ['a', 'b']:
            self.episode(name, True)
        (self.root/'a/evaluation-private.jsonl').write_text('\n'.join(json.dumps(dict(time=t, task_completed=False)) for t in [9., 9.8]))
        (self.root/'b/evaluation-private.jsonl').write_text(json.dumps(dict(time=9.9, task_completed=True)))
        cases = score(self.root)['cases']
        self.assertTrue(cases[0]['false_confirmation'])
        self.assertFalse(cases[0]['independent_settle_success'])
        self.assertIsNone(cases[1]['independent_settle_success'])
        self.assertFalse(cases[1]['false_confirmation'])


if __name__ == '__main__':
    unittest.main()
