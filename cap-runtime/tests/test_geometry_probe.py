import unittest
import numpy as np
from scipy.spatial.transform import Rotation
from experiments.public_cube_geometry import fit_faces, estimate_center, public_size_reference


def cube_faces(face_count=3, rotated=True):
    axis = np.linspace(-.02, .02, 22)
    a, b = np.meshgrid(axis, axis)
    clouds = []
    for i in range(face_count):
        points = np.zeros((a.size, 3))
        points[:, i] = .02
        points[:, (i+1) % 3] = a.ravel()
        points[:, (i+2) % 3] = b.ravel()
        clouds.append(points)
    rotation = Rotation.from_euler('xyz', [17, -24, 31], degrees=True) if rotated else Rotation.identity()
    center = np.array([.5, -.1, .2])
    return rotation.apply(np.concatenate(clouds))+center, rotation.apply([.4, .4, .4])+center, center


class GeometryProbeTests(unittest.TestCase):
    def test_rotated_three_faces_recover_center(self):
        points, camera, center = cube_faces()
        faces = fit_faces(points, camera)
        self.assertEqual(faces['status'], 'faces_observed', faces)
        prior = public_size_reference(faces)
        self.assertIsNotNone(prior)
        result = estimate_center(faces, prior)
        self.assertEqual(result['status'], 'estimated')
        np.testing.assert_allclose(result['center_xyz'], center, atol=.003)

    def test_single_or_two_faces_are_not_a_complete_pose(self):
        for count in [1, 2]:
            points, camera, _ = cube_faces(count)
            result = estimate_center(fit_faces(points, camera), {'side_m': .04})
            self.assertEqual(result['status'], 'unknown', result)
            self.assertNotIn('center_xyz', result)

    def test_no_size_reference_does_not_invent_dimensions(self):
        points, camera, _ = cube_faces()
        result = estimate_center(fit_faces(points, camera), None)
        self.assertEqual(result['reason'], 'no_earlier_public_size_reference')


if __name__ == '__main__':
    unittest.main()
