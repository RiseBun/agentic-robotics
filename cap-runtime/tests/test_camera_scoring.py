import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from experiments.score_camera_trial import score


class CameraScoringGateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.write(self.root/'plan.json',dict(seed=12,task='cube_restack',scope='fixture gate only',conditions=[['primary',''],['wrist','robot0_eye_in_hand']]))
        for label,camera in [('primary',''),('wrist','robot0_eye_in_hand')]:
            folder=self.root/('fixed-'+label)
            self.write(folder/'protocol.json',dict(seed=12,tasks=['cube_restack'],secondary_camera=camera,controller_sha256='same',
                runtime=dict(source_digest='same',upstream_revision='same',upstream_source_digest='same',features=dict(support_second_view=camera))))
            self.write(folder/'results.json',[dict(task='cube_restack',episode_id='episode')])
            self.write(folder/'episode/episode.json',dict(status='closed'))
            (folder/'episode/events.jsonl').write_text(json.dumps(dict(kind='episode_closed'))+'\n')
    def tearDown(self):self.temp.cleanup()
    def write(self,path,value):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))
    @patch('experiments.score_camera_trial.score_closed_trial')
    def test_one_unclosed_condition_prevents_any_private_evaluator_call(self,evaluate):
        self.write(self.root/'fixed-wrist/episode/episode.json',dict(status='ready'))
        with self.assertRaises(RuntimeError):score(self.root)
        evaluate.assert_not_called()
    @patch('experiments.score_camera_trial.score_closed_trial')
    def test_mixed_sources_prevent_any_private_evaluator_call(self,evaluate):
        p=self.root/'fixed-wrist/protocol.json';v=json.loads(p.read_text());v['controller_sha256']='changed';self.write(p,v)
        with self.assertRaises(RuntimeError):score(self.root)
        evaluate.assert_not_called()
    @patch('experiments.score_camera_trial.score_closed_trial')
    def test_all_closed_frozen_conditions_admit_separate_offline_evaluation(self,evaluate):
        evaluate.return_value=dict(cases=[dict(public_at_close='unknown',independent_settle_success=False)])
        result=score(self.root)
        self.assertEqual(evaluate.call_count,2)
        self.assertEqual([x['condition'] for x in result['cases']],['primary','wrist'])
