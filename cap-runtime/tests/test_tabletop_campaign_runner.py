import json
import tempfile
import unittest
from pathlib import Path
from experiments.tabletop_campaign import closed_run_result

class FrozenDenominatorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.physical=self.root/'physical/episode';self.physical.mkdir(parents=True)
        (self.physical/'episode.json').write_text(json.dumps(dict(status='closed',active_segment=None)))
        (self.root/'result.json').write_text(json.dumps(dict(status='needs_attention',episode=dict(status='failed',termination_reason='budget_exhausted'))))
    def tearDown(self):self.temp.cleanup()
    def test_budget_failure_is_retained_after_confirmed_close(self):
        self.assertEqual(closed_run_result(self.root,0)['status'],'needs_attention')
    def test_unclosed_task_blocks_scoring_and_next_dispatch(self):
        (self.physical/'episode.json').write_text(json.dumps(dict(status='ready',active_segment=None)))
        with self.assertRaisesRegex(RuntimeError,'close unconfirmed'):closed_run_result(self.root,0)
    def test_crashed_runner_cannot_be_replaced_by_an_old_result(self):
        with self.assertRaisesRegex(RuntimeError,'protocol failure'):closed_run_result(self.root,1)
    def test_missing_result_is_protocol_failure(self):
        (self.root/'result.json').unlink()
        with self.assertRaisesRegex(RuntimeError,'protocol failure'):closed_run_result(self.root,0)

if __name__=='__main__':unittest.main()
