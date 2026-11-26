"""A newer stop accepted during expiry stepping must be owner-confirmed before exit."""
import tempfile,threading,time,unittest
from agentic_cap.core import Manager
from agentic_cap.fixture import FixtureBackend

class ExpiryHoldRaceTests(unittest.TestCase):
    def test_expiry_drains_new_hold_arriving_during_inflight_owner_step(self):
        with tempfile.TemporaryDirectory() as folder:
            manager=Manager(folder,FixtureBackend)
            state=manager.create('race','cube_lifting',1,{'holding':'red cube'})
            e=manager.episodes[state['id']];entered=threading.Event();release=threading.Event();results=[];errors=[]
            def until(fn):
                end=time.monotonic()+4
                while time.monotonic()<end:
                    if fn():return
                    time.sleep(.005)
                self.fail('Timed out')
            try:
                until(lambda:e.snapshot and e.status=='ready')
                original=e.backend.step
                def stepping(target,gripper):
                    if e.status=='budget_exhausted' and not entered.is_set():
                        entered.set();release.wait(3)
                    return original(target,gripper)
                e.backend.step=stepping
                e.limits['wall_seconds']=time.monotonic()-e.started+.04
                self.assertTrue(entered.wait(3))
                def cancel():
                    try:results.append(e.cancel())
                    except Exception as error:errors.append(error)
                caller=threading.Thread(target=cancel);caller.start()
                until(lambda:e.hold_serial>e.expiration_hold_serial)
                release.set();caller.join(4)
                self.assertFalse(caller.is_alive());self.assertEqual(errors,[])
                self.assertTrue(results[0]['stopped'])
                until(lambda:e.status=='closed')
                self.assertGreaterEqual(e.hold_ack,e.hold_serial)
                self.assertEqual(e.termination_reason,'wall_budget_exhausted')
            finally:release.set();e.close()
if __name__=='__main__':unittest.main()
