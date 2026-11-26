"""Independent evaluation contract tests; synthetic rows are not robot results."""
import json
import tempfile
import unittest
from pathlib import Path
from agentic_cap.tabletop_evaluation import score_episode,score_closed_batch


class IndependentEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.folder=self.root/'episode';self.folder.mkdir()
        self.episode=dict(id='episode',seed=201,status='closed',active_segment=None,goal={'all':[dict(predicate='holding',object='block_a')]},used={})
        self.rows=[dict(control_step=i,simulation_time=i*.2,objects={'block_a':dict(position=[0.,0.,.95],quaternion=[1,0,0,0])},
            half_extents={'block_a':[.02,.02,.02]},tcp_xyz=[0,0,.96],gripper_command=0.) for i in range(7)]
        self.write()
    def tearDown(self):self.temp.cleanup()
    def write(self):
        (self.folder/'episode.json').write_text(json.dumps(self.episode))
        (self.folder/'evaluation-private.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in self.rows))
        (self.folder/'events.jsonl').write_text(json.dumps(dict(kind='finish_checked',result=dict(status='verified')))+'\n')
    def test_expected_user_goal_is_scored_independently_of_model_interpretation(self):
        actual=score_episode(self.folder);self.assertTrue(actual['physical_verified'])
        expected={'all':[dict(predicate='in_region',object='block_a',region='left')]}
        result=score_episode(self.folder,expected)
        self.assertFalse(result['physical_verified']);self.assertTrue(result['false_completion'])
        self.assertFalse(result['registered_goal_matches_expected'])
    def test_small_real_region_violation_is_not_hidden_by_scoring_tolerance(self):
        self.episode['goal']={'all':[dict(predicate='in_region',object='block_a',region='left')]}
        for row in self.rows:
            row['objects']['block_a']['position']=[-.0601,-.16,.82166]
            row['half_extents']['block_a']=[.02166]*3
            row['gripper_command']=1.
        self.write();self.assertTrue(score_episode(self.folder)['false_completion'])
    def test_region_goal_does_not_require_upright_orientation(self):
        self.episode['goal']={'all':[dict(predicate='in_region',object='block_a',region='left')]}
        for row in self.rows:
            row['objects']['block_a'].update(position=[-.12,-.16,.82],quaternion=[.70710678118,.70710678118,0,0])
            row['gripper_command']=1.
        self.write();self.assertTrue(score_episode(self.folder)['physical_verified'])

    def test_open_task_cannot_read_private_scoring(self):
        self.episode.update(status='ready');self.write()
        with self.assertRaisesRegex(ValueError,'closed'):score_episode(self.folder)
    def test_incomplete_physical_window_remains_unscored(self):
        self.rows=self.rows[-2:];self.write()
        self.assertEqual(score_episode(self.folder)['status'],'unscored')
    def test_batch_refuses_scoring_until_every_episode_closed(self):
        self.episode.update(status='ready');self.write()
        with self.assertRaisesRegex(ValueError,'Close every'):score_closed_batch(self.root)

if __name__=='__main__':unittest.main()
