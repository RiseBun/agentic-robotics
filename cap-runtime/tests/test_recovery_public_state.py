import tempfile
import unittest
from types import MethodType

from agentic_cap.cap_backend import CaPBackend
from agentic_cap.core import Manager
from agentic_cap.fixture import FixtureBackend
from test_runtime import until


class PublicRecoveryStateTests(unittest.TestCase):
    def test_current_protocol_metadata_does_not_reuse_old_image_session_counters(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = Manager(directory, FixtureBackend)
            state = manager.create('public-recovery', 'cube_stack', 1,
                                   {'on': ['red cube', 'green cube'], 'empty_gripper': True})
            e = manager.episodes[state['id']]
            try:
                until(lambda: e.status == 'ready' and e.snapshot)
                backend = e.backend
                backend.public_recovery_state = MethodType(CaPBackend.public_recovery_state, backend)
                with e.lock:
                    backend.held_hint = 'red cube'
                    backend.inspection_sessions = {('green cube', 'red cube'):
                        {'tried': [[.5, .1, .2], [.5, -.1, .2]], 'blocked': False}}
                    backend.placement_release = {'id': 'release', 'tried': [[.5, .1, .3]], 'blocked': True}
                    old = e.observe()
                    self.assertEqual(old['bounded_recovery_state']['support_sessions'][0]['views_used'], 2)
                    self.assertEqual(old['bounded_recovery_state']['placement_session']['views_used'], 1)
                    self.assertTrue(old['bounded_recovery_state']['placement_session']['blocked'])
                    # Successful release/new grasp lifecycle clears these
                    # sessions before the next sensor publication.
                    backend.inspection_sessions.clear()
                    backend.placement_release = None
                    backend.held_hint = None
                    new = e.observe()
                    self.assertEqual(new['evidence_ref'], old['evidence_ref'])
                    self.assertEqual(new['bounded_recovery_state']['support_sessions'], [])
                    self.assertIsNone(new['bounded_recovery_state']['placement_session'])
                    self.assertIsNone(new['bounded_recovery_state']['holding_identity_hint'])
                    self.assertEqual(old['bounded_recovery_state']['support_sessions'][0]['views_used'], 2)
                    self.assertIn('requires fresh public verification', new['bounded_recovery_state']['scope'])
            finally:
                e.close()


if __name__ == '__main__':
    unittest.main()
