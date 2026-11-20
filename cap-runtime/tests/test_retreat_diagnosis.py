"""Diagnosis sensor faults and offline admission do not bypass public boundaries."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from experiments.vertical_retreat_trial import DescentFaultBackend
from experiments.score_vertical_retreat import score
from agentic_cap.safe_retreat import descent_anchor


class RetreatDiagnosisTests(unittest.TestCase):
    def test_descent_from_requested_instead_of_measured_xy_is_not_vertical(self):
        b = SimpleNamespace(episode=SimpleNamespace(goal={'on': ['red cube', 'green cube']}),
                            holding_results={'red cube': 'verified'})
        current = dict(tcp_xyz=[.672, -.107, .093], tcp_wxyz=[0., 0., 1., 0.],
                       last_gripper_command='closed', evidence_ref='frame')
        self.assertIsNone(descent_anchor(b, current, [.670, -.114, -.027], current['tcp_wxyz']))
        target = current['tcp_xyz'].copy()
        target[2] -= .12
        self.assertIsNotNone(descent_anchor(b, current, target, current['tcp_wxyz']))

    def backend(self, mode):
        b = object.__new__(DescentFaultBackend)
        b.diagnosis_mode, b.diagnosis_anchor, b.diagnosis_cleared = mode, None, False
        b.events, b.encoder, b.xyz = [], [], [.5, 0., .10]
        b.episode = SimpleNamespace(event=lambda kind, **data: b.events.append(dict(kind=kind, **data)))
        b.monitor = SimpleNamespace(fk=lambda q: (np.array(b.xyz), np.array([0., 0., 1., 0.])))
        return b

    def test_fault_requires_recorded_descent_then_uses_measured_retreat(self):
        b = self.backend('retreat_recovers')
        rgb, depth = np.full((8, 8, 3), [0, 200, 0], dtype=np.uint8), np.ones((8, 8))
        self.assertFalse(b.apply_camera_fault(rgb, depth)[2])
        b.safe_retreat = dict(start_xyz=[.5, 0., .22])
        masked = b.apply_camera_fault(rgb, depth)
        self.assertTrue(masked[2])
        self.assertFalse(np.array_equal(masked[0], rgb))
        self.assertFalse(np.array_equal(masked[1], depth))
        b.safe_retreat = None  # The guard consumes the anchor before motion.
        self.assertTrue(b.apply_camera_fault(rgb, depth)[2])
        b.xyz[2] = .22
        self.assertFalse(b.apply_camera_fault(rgb, depth)[2])
        self.assertEqual([e['kind'] for e in b.events],
                         ['diagnostic_descent_fault_armed', 'diagnostic_descent_fault_cleared'])

    def test_persistent_fault_does_not_clear_on_retreat(self):
        b = self.backend('persistent_unknown')
        b.safe_retreat = dict(start_xyz=[.5, 0., .22])
        rgb, depth = np.full((8, 8, 3), [0, 200, 0], dtype=np.uint8), np.ones((8, 8))
        self.assertTrue(b.apply_camera_fault(rgb, depth)[2])
        b.xyz[2] = .22
        self.assertTrue(b.apply_camera_fault(rgb, depth)[2])

    def test_unfinished_or_unclosed_case_rejects_before_any_truth_read(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'protocol.json').write_text(json.dumps(dict(cases=['a', 'b'])))
            (root/'results.json').write_text(json.dumps([dict(mode='a', episode_id='a')]))
            with self.assertRaisesRegex(ValueError, 'All frozen retreat cases'):
                score(root)
            (root/'results.json').write_text(json.dumps([dict(mode=n, episode_id=n) for n in ['a', 'b']]))
            for name in ['a', 'b']:
                d = root/name
                d.mkdir()
                status = 'closed' if name == 'a' else 'ready'
                (d/'episode.json').write_text(json.dumps(dict(status=status)))
                (d/'events.jsonl').write_text(json.dumps(dict(kind='episode_closed' if name == 'a' else 'episode_ready'))+'\n')
            # Neither private file exists: b must reject before opening a.
            with self.assertRaisesRegex(ValueError, 'authoritative close'):
                score(root)
