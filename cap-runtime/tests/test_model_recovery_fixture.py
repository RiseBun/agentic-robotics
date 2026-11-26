"""Validate synthetic fault behavior before interpreting real-model probe results."""
import os,tempfile,time,unittest
from unittest.mock import patch
from agentic_cap.core import Manager
from experiments.model_recovery_fixture import RecoveryFixture

class RecoveryFaultFixtureTests(unittest.TestCase):
    def run_case(self,mode):
        with patch.dict(os.environ,MODEL_RECOVERY_FAULT=mode),tempfile.TemporaryDirectory() as folder:
            manager=Manager(folder,RecoveryFixture)
            s=manager.create('fault',scene='tabletop_v1',seed=201,goal={'all':[dict(predicate='on',object='block_a',support='platform'),dict(predicate='empty_gripper')]})
            e=manager.episodes[s['id']];serial=0
            def until(fn):
                end=time.monotonic()+5
                while time.monotonic()<end:
                    if fn():return
                    time.sleep(.01)
                self.fail('Timeout')
            def execute(name,args):
                nonlocal serial
                serial+=1
                e.submit_plan('p'+str(serial),e.revision,{'nodes':[dict(id='n'+str(serial),skill=dict(name=name,arguments=args))]})
                system=e.observe()['system'];node=system['task']['nodes'][0]
                sid=e.execute_skill('s'+str(serial),e.revision,system['state_id'],node['id'],dict(name=name,arguments=args))
                until(lambda:e.state()['active_segment'] is None)
                return e.state()['segments'][sid]
            try:
                until(lambda:e.snapshot and e.status=='ready')
                execute('acquire_object',{'object':'block_a'})
                failed=execute('place_object',{'object':'block_a','support':'platform'})
                self.assertEqual(failed['execution'],'failed');self.assertEqual(e.backend.held,'block_a')
                self.assertTrue(e.backend.fault_active);self.assertTrue(e.backend.fault_triggered)
                missing={'all':[dict(predicate='support_geometry',object='platform')]}
                self.assertEqual(e.backend.verify(missing,e.observe())['status'],'unknown')
                # Restoration changes pose, not support evidence.
                execute('restore_grip_pose',{'object':'block_a'})
                self.assertTrue(e.backend.fault_active)
                looked=execute('inspect_object',{'object':'platform','evidence':'support_geometry'})
                if mode=='recoverable':
                    self.assertEqual(looked['execution'],'completed');self.assertFalse(e.backend.fault_active)
                    result=execute('place_object',{'object':'block_a','support':'platform'})
                    self.assertEqual(result['goal_verification']['status'],'verified')
                else:
                    self.assertEqual(looked['execution'],'failed');self.assertTrue(e.backend.fault_active)
                    self.assertEqual(e.backend.fault_views,2)
                    second=execute('inspect_object',{'object':'platform','evidence':'support_geometry'})
                    self.assertEqual(second['execution'],'failed');self.assertEqual(e.backend.fault_views,2)
                    self.assertNotEqual(e.finish(e.revision)['status'],'verified')
                self.assertEqual(e.backend.reset_count,1)
            finally:e.close()
    def test_available_active_view_is_required_to_recover(self):self.run_case('recoverable')
    def test_persistent_unknown_cannot_be_released_or_extend_view_budget(self):self.run_case('persistent')
if __name__=='__main__':unittest.main()
