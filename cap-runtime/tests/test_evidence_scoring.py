import json
import tempfile
import unittest
from pathlib import Path

from experiments.score_evidence_campaign import evidence_metrics, score


class EvidenceScoringTests(unittest.TestCase):
    def test_metrics_distinguish_restored_evidence_and_success(self):
        events = [dict(kind='api_started', name='inspect_support'),
                  dict(kind='view_attempt', acquisition_id='a', target_xyz=[0, .1, 0], target_wxyz=[1, 0, 0, 0]),
                  dict(kind='view_attempt', acquisition_id='a', target_xyz=[0, .1, 0], target_wxyz=[1, 0, 0, 0]),
                  dict(kind='evidence_acquisition_finished', status='available', before={'status': 'unknown'})]
        result = evidence_metrics(events)
        self.assertEqual(result['unknown_to_available'], 1)
        self.assertEqual(result['duplicate_views_within_acquisition'], 1)
        self.assertEqual(result['inspect_support_calls'], 1)
        self.assertIsNone(result['unknown_evidence_release_violations'])

    def test_post_placement_verification_is_separate_from_support_visibility(self):
        result = evidence_metrics([
            dict(kind='api_started', name='inspect_placement'),
            dict(kind='evidence_acquisition_finished', acquisition_type='placement_verification',
                 status='verified', before={'status': 'unknown'})])
        self.assertEqual(result['unknown_to_available'], 0)
        self.assertEqual(result['inspect_placement_calls'], 1)
        self.assertEqual(result['placement_unknown_to_verified'], 1)

    def test_diagnosis_requires_every_case_and_every_close_before_private_read(self):
        from experiments.score_placement_diagnosis import score as score_diagnosis
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'protocol.json').write_text(json.dumps(dict(cases=['a', 'b'])))
            (root/'results.json').write_text(json.dumps([dict(mode='a')]))
            with self.assertRaisesRegex(ValueError, 'Every frozen diagnosis'):
                score_diagnosis(root)
            (root/'results.json').write_text(json.dumps([dict(mode='a', episode_id='a'), dict(mode='b', episode_id='b')]))
            for name, kind in [('a', 'episode_closed'), ('b', 'episode_ready')]:
                (root/name).mkdir()
                (root/name/'events.jsonl').write_text(json.dumps(dict(kind=kind))+'\n')
            # Neither private file exists: b must reject before reading a.
            with self.assertRaisesRegex(ValueError, 'Authoritative episode close'):
                score_diagnosis(root)

    def test_private_files_are_not_read_before_all_results_exist(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'paired-campaign.json').write_text(json.dumps(dict(schedule=[dict(pair_id='p', variant='baseline', task='cube_stack', seed=61)])))
            with self.assertRaisesRegex(ValueError, 'All scheduled trials'):
                score(root, {'baseline': root/'absent-private'})

    def test_unclosed_episode_rejected_before_private_scoring(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            schedule = dict(pair_id='p', variant='baseline', task='cube_stack', seed=61)
            (root/'paired-campaign.json').write_text(json.dumps(dict(schedule=[schedule])))
            result = root/'p/baseline/cube_stack-s61-r0'
            result.mkdir(parents=True)
            (result/'result.json').write_text(json.dumps(dict(outcome='completed', episode_id='e')))
            private = root/'private/e'
            private.mkdir(parents=True)
            (private/'events.jsonl').write_text(json.dumps(dict(kind='episode_ready'))+'\n')
            with self.assertRaisesRegex(ValueError, 'authoritative close'):
                score(root, {'baseline': root/'private'})

    def test_recovered_connection_uses_final_runtime_cost_instead_of_stale_client_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            schedule = dict(pair_id='p', variant='baseline', task='cube_stack', seed=63)
            (root/'paired-campaign.json').write_text(json.dumps(dict(schedule=[schedule], model='fixture', settle_milliseconds=1250)))
            result = root/'p/baseline/cube_stack-s63-r0'
            result.mkdir(parents=True)
            (result/'result.json').write_text(json.dumps(dict(outcome='infrastructure_error', episode_id='e',
                                                             error='connection reset', used_at_close={'wall_seconds': 83})))
            private = root/'private/e'
            private.mkdir(parents=True)
            (private/'events.jsonl').write_text(json.dumps(dict(kind='episode_closed', time=20))+'\n')
            (private/'episode.json').write_text(json.dumps(dict(id='e', status='closed', used={'wall_seconds': 1025})))
            (private/'evaluation-private.jsonl').write_text('\n'.join(json.dumps(dict(time=t, task_completed=True)) for t in [19, 19.7]))
            result = score(root, {'baseline': root/'private'})
            self.assertEqual(result['rows'][0]['used']['wall_seconds'], 1025)
            self.assertEqual(result['rows'][0]['client_recorded_used_at_close']['wall_seconds'], 83)
            self.assertEqual(result['groups']['baseline']['infrastructure_errors'], 1)
            self.assertEqual(result['groups']['baseline']['independent_physical_successes'], 1)
            self.assertEqual(result['groups']['baseline']['verified_system_successes'], 0)


if __name__ == '__main__':
    unittest.main()
