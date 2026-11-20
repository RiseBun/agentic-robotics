import json
from pathlib import Path
import tempfile
import unittest

from experiments.score_retreat_model import score


class ModelScoringTests(unittest.TestCase):
    def write_case(self, root, name, closed=True):
        folder=root/'campaign'/name
        folder.mkdir(parents=True)
        (folder/'result.json').write_text(json.dumps(dict(episode_id=name, source_unchanged=True, outcome='completed',
            public_at_decision={'status':'verified'}, started_at=2, terminal_at=10)))
        episode=root/(name+'-records')/name
        episode.mkdir(parents=True)
        (episode/'episode.json').write_text(json.dumps(dict(id=name,status='closed' if closed else 'ready',used={})))
        events=[dict(id=1,kind='backend_configured',time=0,reset_count=1),
                dict(id=2,kind='diagnostic_model_handoff_ready',time=.5),
                dict(id=3,kind='placement_retreat_checked',time=1,allowed=True),
                dict(id=4,kind='api_started',name='open_gripper',time=3,call_id='open'),
                dict(id=5,kind='api_result',name='open_gripper',time=4,call_id='open'),
                dict(id=6,kind='episode_closed' if closed else 'episode_ready',time=12)]
        (episode/'events.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
        return episode

    def protocol(self, root, names):
        (root/'campaign').mkdir(exist_ok=True)
        (root/'campaign/protocol.json').write_text(json.dumps(dict(model='fixture',sources={'digest':'x'},
            cases=[dict(mode=n,episode_id=n,diagnostic={'handoff_event_id':2,'preparation_used':{}}) for n in names])))

    def test_all_cases_close_before_any_truth_read(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            self.protocol(root,['a','b'])
            self.write_case(root,'a')
            self.write_case(root,'b',False)
            with self.assertRaisesRegex(ValueError,'authoritative close'):
                score(root)  # No private file exists, including for a.

    def test_intentional_opening_is_not_scored_as_pre_release_contact_loss(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            self.protocol(root,['a'])
            episode=self.write_case(root,'a')
            samples=[dict(time=t,task_completed=True,bilateral_contact={'red cube':held})
                     for t,held in [(1.5,True),(2.5,True),(3.5,False),(10.9,False),(11.6,False)]]
            (episode/'evaluation-private.jsonl').write_text('\n'.join(json.dumps(s) for s in samples))
            row=score(root)['rows'][0]
            self.assertEqual(row['contact_before_intentional_release']['status'],'verified')
            self.assertEqual(row['contact_before_intentional_release']['sample_count'],2)
            self.assertEqual(row['successful_releases'],1)
            self.assertFalse(row['false_completion'])
