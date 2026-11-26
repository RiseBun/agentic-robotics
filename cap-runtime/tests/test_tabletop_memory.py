import copy
import tempfile
import unittest
from agentic_cap.tabletop_memory import parameterize_trace,SkillLibrary
from agentic_cap.tabletop_campaign import campaign_manifest,admit_campaign


class MemoryTests(unittest.TestCase):
    def trace(self):
        return dict(episode=dict(id='dev',status='closed',task='tabletop',seed=201),events=[
            dict(id=1,kind='api_error'),dict(id=2,kind='segment_finished',segment=dict(execution='completed',
                node_verification=dict(status='verified'),skill=dict(name='inspect_object',arguments=dict(object='block_a'))))])
    def test_candidate_is_parameterized_and_not_automatically_published(self):
        c=parameterize_trace(self.trace());self.assertNotIn('block_a',str(c['steps']))
        with tempfile.TemporaryDirectory() as root:
            library=SkillLibrary(root);library.save_candidate(c)
            self.assertEqual(library.retrieve(dict(world=dict(fresh=True),skills=[dict(name='inspect_object')])),[])
            with self.assertRaises(ValueError):library.publish(c['id'])
    def test_success_claim_or_missing_failure_cannot_create_repair(self):
        t=self.trace();t['episode']['status']='ready'
        with self.assertRaises(ValueError):parameterize_trace(t)
        t=self.trace();t['events']=t['events'][1:]
        with self.assertRaises(ValueError):parameterize_trace(t)
    def test_repair_variables_only_bind_steps_after_the_last_failure(self):
        trace=self.trace()
        trace['events'].extend([dict(id=3,kind='api_error'),dict(id=4,kind='segment_finished',segment=dict(execution='completed',
            node_verification=dict(status='verified'),skill=dict(name='acquire_object',arguments=dict(object='block_b'))))])
        candidate=parameterize_trace(trace)
        self.assertEqual(candidate['variables'],[dict(name='arg1',type='object')])
        self.assertEqual(candidate['steps'][0]['arguments'],dict(object='$arg1'))
        self.assertEqual(len(candidate['failure_evidence']),2)

    def test_failed_independent_validation_does_not_promote(self):
        c=parameterize_trace(self.trace())
        with tempfile.TemporaryDirectory() as root:
            library=SkillLibrary(root);library.save_candidate(c)
            with self.assertRaises(ValueError):library.validate(c['id'],[dict(success=True)])
    def test_campaign_is_frozen_and_gate_controlled(self):
        m=campaign_manifest('model','runtime','library');self.assertEqual(len(m['runs']),54)
        self.assertEqual({r['seed'] for r in m['runs']},{701,702,703})
        with self.assertRaisesRegex(ValueError,'gate A'):admit_campaign({},m,'library')
        gates={k:dict(status='passed') for k in ['A','B','C','D_validation']}
        with self.assertRaisesRegex(ValueError,'library changed'):admit_campaign(gates,m,'changed')
        self.assertTrue(admit_campaign(gates,m,'library'))

class FrozenInputTests(unittest.TestCase):
    def test_modified_goal_or_description_cannot_be_admitted(self):
        m=campaign_manifest('model','runtime','library');m['tasks'][0]['text']='Another goal'
        gates={k:dict(status='passed') for k in ['A','B','C','D_validation']}
        with self.assertRaisesRegex(ValueError,'altered'):admit_campaign(gates,m,'library')
    def test_rehashed_condition_change_still_cannot_replace_the_frozen_design(self):
        from agentic_cap.tabletop_domain import digest
        m=campaign_manifest('model','runtime','library');m['runs'][0]['condition']='system_no_memory'
        m['manifest_id']=digest({k:v for k,v in m.items() if k!='manifest_id'})
        gates={k:dict(status='passed') for k in ['A','B','C','D_validation']}
        with self.assertRaisesRegex(ValueError,'conditions changed'):admit_campaign(gates,m,'library')

if __name__=='__main__':unittest.main()
