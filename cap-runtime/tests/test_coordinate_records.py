"""Frame metadata prevents repeating the world/base mismatch in offline scoring."""
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import numpy as np
from agentic_cap.public_sensors import public_packet, PublicExecutionMonitor, mask_public_support
from agentic_cap.evaluator import PrivateEvaluator


class CoordinateRecordTests(unittest.TestCase):
    def test_public_support_fault_masks_only_the_public_rgbd_packet(self):
        rgb = np.zeros((20, 20, 3), dtype=np.uint8)
        rgb[8:12, 9:13] = [10, 180, 10]
        depth = np.ones((20, 20, 1), dtype=np.float32)
        masked, masked_depth, changed = mask_public_support(rgb, depth)
        self.assertTrue(changed)
        self.assertEqual(int(masked[10, 11].sum()), 0)
        self.assertEqual(float(masked_depth[10, 11, 0]), 0.)
        self.assertEqual(int(rgb[10, 11].sum()), 200)

    def test_public_packet_and_state_label_frames_without_truth(self):
        observation = dict(robot_joint_pos=np.zeros(7), robot0_gripper_qpos=np.zeros(2),
                           robot0_robotview=dict(images=dict(rgb=np.zeros((8, 8, 3), dtype=np.uint8),
                                                            depth=np.ones((8, 8, 1))),
                                                intrinsics=np.eye(3), pose=np.array([0, 0, 0, 1, 0, 0, 0])),
                           world_positions={'secret': 'must not pass'}, contacts=['must not pass'])
        packet = public_packet(observation)
        self.assertEqual(packet['coordinate_frame'], 'cap_robot_base')
        self.assertNotIn('world_positions', packet)
        self.assertNotIn('contacts', packet)
        # All packet values can be archived without Python object arrays.
        stream = io.BytesIO()
        np.savez_compressed(stream, **packet)
        with np.load(io.BytesIO(stream.getvalue()), allow_pickle=False) as saved:
            self.assertTrue(all(saved[k].dtype.kind != 'O' for k in saved.files))
        monitor = PublicExecutionMonitor(None, lambda: packet)
        monitor.fk = lambda joints: (np.zeros(3), np.array([1, 0, 0, 0]))
        state = monitor.state()
        self.assertEqual(state['coordinate_convention']['quaternion_order'], 'WXYZ')
        self.assertEqual(state['coordinate_convention']['length_unit'], 'm')
        self.assertNotIn('world_from_cap_robot_base_xyz_wxyz', state)

    def test_private_record_uses_actual_base_pose_not_fixed_xml_offset(self):
        with tempfile.TemporaryDirectory() as directory:
            # Deliberately use a moved/rotated base, so a hardcoded transform fails.
            data = SimpleNamespace(body_xpos=np.array([[.1, .2, .3], [-.7, .3, .9]]),
                                   body_xquat=np.array([[1, 0, 0, 0], [.7071, 0, 0, .7071]]),
                                   contact=[], ncon=0)
            rs = SimpleNamespace(cube_body_id=0, sim=SimpleNamespace(data=data))
            low = SimpleNamespace(robosuite_env=rs, base_link_idx=1)
            evaluator = PrivateEvaluator(low, Path(directory))
            evaluator.sample(SimpleNamespace(id='test', active=None, revision=1, goal={'holding': 'red cube'}))
            row = json.loads((Path(directory)/'evaluation-private.jsonl').read_text())
            self.assertEqual(row['world_from_cap_robot_base_xyz_wxyz'], [-.7, .3, .9, .7071, 0, 0, .7071])
            self.assertEqual(row['coordinate_convention']['positions_frame'], 'world')


if __name__ == '__main__':
    unittest.main()
