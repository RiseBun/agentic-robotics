import copy,unittest
from types import SimpleNamespace
import numpy as np
from agentic_cap.tabletop_restore import assess_recorded_descent

class RestoreDiagnosisTests(unittest.TestCase):
    def data(self):
        record=dict(object='block_a',revision=1,trajectory=SimpleNamespace(joint_positions=np.zeros((2,7))),other_versions={'platform':3,'block_b':1})
        snap=dict(last_gripper_command='closed',object_versions={'platform':3,'block_b':1})
        return record,snap
    def test_missing_record_is_distinguished(self):
        r=assess_recorded_descent(None,'block_a',1,np.zeros(7),{})
        self.assertEqual(r['blockers'],['record_present']);self.assertEqual(r['status'],'unknown')
    def test_each_guard_retains_its_original_admission_effect(self):
        for cause in ['object_matches','revision_matches','gripper_closed','encoder_at_endpoint','scene_versions_match']:
            record,snap=self.data();obj='block_a';revision=1;encoder=np.zeros(7)
            if cause=='object_matches':obj='block_b'
            if cause=='revision_matches':revision=2
            if cause=='gripper_closed':snap['last_gripper_command']='open'
            if cause=='encoder_at_endpoint':encoder[0]=.051
            if cause=='scene_versions_match':snap['object_versions']['platform']=4
            result=assess_recorded_descent(record,obj,revision,encoder,snap)
            self.assertEqual(result['blockers'],[cause]);self.assertEqual(result['status'],'unknown')
    def test_multiple_failures_are_reported_without_hiding_scene_mismatch(self):
        record,snap=self.data();snap['last_gripper_command']='open';snap['object_versions']={}
        result=assess_recorded_descent(record,'block_a',2,np.ones(7),snap)
        self.assertEqual(set(result['blockers']),{'revision_matches','gripper_closed','encoder_at_endpoint','scene_versions_match'})
        self.assertEqual(set(result['details']['changed_objects']),{'platform','block_b'})
    def test_exact_endpoint_threshold_is_not_relaxed(self):
        record,snap=self.data()
        self.assertEqual(assess_recorded_descent(record,'block_a',1,np.full(7,.05),snap)['status'],'unknown')
        self.assertEqual(assess_recorded_descent(record,'block_a',1,np.full(7,.0499),snap)['status'],'verified')
    def test_diagnostic_does_not_change_scene_or_record(self):
        record,snap=self.data();before=copy.deepcopy(snap)
        result=assess_recorded_descent(record,'block_a',1,np.zeros(7),snap)
        self.assertEqual(result['blockers'],[]);self.assertEqual(snap,before)
        self.assertEqual(record['other_versions'],before['object_versions'])
if __name__=='__main__':unittest.main()
