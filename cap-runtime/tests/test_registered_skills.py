"""Exercise real skill programs on synthetic public sensors, not physical scoring."""
import copy
import tempfile
import time
import unittest

from agentic_cap.core import DependenciesChanged, Manager
from agentic_cap.fixture import FixtureBackend
from agentic_cap.skills import requirements_for
from test_runtime import until


class PublicSkillFixture(FixtureBackend):
    def __init__(self, episode):
        super().__init__(episode)
        self.xyz, self.quat = [.45, 0., .2], [0., 0., 1., 0.]
        self.last_action_tick = 0
        self.positions = {'red cube': [.45, 0., .02], 'green cube': [.6, 0., .02]}
        self.moves = []
        self.offset_available, self.bounds_available = True, True
        self.route = None
        self.low_holding_unknown = False

    def capture(self):
        objects = {}
        for name, position in self.positions.items():
            center = [self.xyz[0], self.xyz[1], self.xyz[2]-.02] if self.held == name else position
            objects[name] = dict(status='visible', surface_median_xyz=center,
                surface_q05_xyz=[x-.02 for x in center], surface_q95_xyz=[x+.02 for x in center],
                surface_span_xyz=[.04]*3)
        if not self.bounds_available:
            objects['green cube'].pop('surface_q95_xyz')
        return dict(backend='public-sensor-fixture', tcp_xyz=self.xyz.copy(), tcp_wxyz=self.quat.copy(),
            objects=objects, control_step=self.ticks, captured_at=time.time(), evidence_ref=f'frame-{self.ticks}',
            support_evidence={name: dict(status='available') for name in objects},
            joint_positions=self.q.copy(), gripper_joint_positions=[.04, -.04],
            last_gripper_command='closed' if self.held else 'open',
            recovery_options=dict(vertical_retreat=copy.deepcopy(self.route) or dict(status='unknown')))

    def public_recovery_state(self):
        return dict(holding_identity_hint=self.held)

    def verify(self, goal, snapshot):
        result = super().verify(goal, snapshot)
        if goal.get('holding') and self.low_holding_unknown and self.xyz[2] < .15:
            return dict(status='unknown', reason='synthetic low-pose occlusion')
        if goal.get('holding') and result['status'] == 'verified' and self.offset_available:
            result['evidence'] = [dict(estimated_center_tcp_offset_xyz=[0., 0., -.02],
                                       reference_tcp_wxyz=self.quat.copy())]
        return result

    def tools(self, segment):
        wait = super().tools(segment)['wait_motion']

        def move(xyz, quat, z_approach=0.):
            wait(3)
            self.episode.checkpoint(segment)
            self.xyz, self.quat = list(xyz), list(quat)
            self.route = None
            self.last_action_tick = self.ticks
            self.moves.append(dict(xyz=self.xyz.copy(), quat=self.quat.copy(), approach=z_approach))
            return dict(status='reached')

        def release():
            self.episode.checkpoint(segment)
            if self.held:
                obj = self.held
                self.positions[obj] = [self.xyz[0], self.xyz[1], self.xyz[2]-.02]
                self.placed = [obj, 'green cube'] if abs(self.xyz[0]-.6) < .001 else None
            self.held = None
            self.last_action_tick = self.ticks

        def grasp():
            self.episode.checkpoint(segment)
            self.held = 'red cube'
            self.last_action_tick = self.ticks

        def state():
            with self.episode.cv:
                while self.episode.snapshot['control_step'] <= self.last_action_tick:
                    self.episode.checkpoint(segment)
                    self.episode.cv.wait(.02)
                return self.episode.observe()

        return dict(goto_pose=move, open_gripper=release, close_gripper=grasp,
            get_object_pose=lambda name: (copy.deepcopy(self.positions[name]), self.quat.copy(), [.04]*3),
            get_execution_state=state, check_holding=lambda name: self.verify(dict(holding=name), state()))


class RegisteredSkillTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        manager = Manager(self.temp.name, PublicSkillFixture)
        episode = manager.create('registered', 'cube_stack', 61,
                                 dict(on=['red cube', 'green cube'], empty_gripper=True))
        self.e = manager.episodes[episode['id']]
        until(lambda: self.e.status == 'ready' and self.e.snapshot)

    def tearDown(self):
        self.e.close()
        self.temp.cleanup()

    def skill(self, kind, name, args):
        system = self.e.observe()['system']
        identity = self.e.execute_skill(name, self.e.revision, system['state_id'],
            f'{system["goal_version"]}:{kind}', dict(name=name, arguments=args))
        until(lambda: self.e.active is None)
        return self.e.segments[identity]

    def hold(self):
        self.e.backend.held = 'red cube'
        until(lambda: self.e.observe()['system']['world']['facts']['holding:red cube']['status'] == 'verified')

    def test_real_skill_programs_share_interpreter_budget_and_runtime_postconditions(self):
        acquired = self.skill('acquire', 'acquire_object', dict(object='red cube'))
        self.assertEqual(acquired['execution'], 'completed', acquired.get('error'))
        self.assertEqual(acquired['skill_verification']['status'], 'verified')
        self.assertNotEqual(acquired['goal_verification']['status'], 'verified')
        placed = self.skill('place', 'place_object', dict(object='red cube', support='green cube'))
        self.assertEqual(placed['execution'], 'completed', placed.get('error'))
        self.assertEqual(placed['skill_verification']['status'], 'verified')
        system = self.e.observe()['system']
        self.assertEqual(system['task']['status'], 'verified')
        self.assertEqual(self.e.finish(self.e.revision)['status'], system['world']['facts']['goal']['status'])
        self.assertEqual(self.e.used['calls'], 12)
        self.assertEqual(self.e.backend.reset_count, 1)
        self.assertNotIn('pick', [call['name'] for call in acquired['api_attempts']])
        self.assertEqual(len(self.e.backend.moves), 5)
        descent, retreat = self.e.backend.moves[-2:]
        self.assertEqual(descent['xyz'][:2], retreat['xyz'][:2])
        self.assertEqual(descent['quat'], retreat['quat'])

    def test_missing_measured_holding_geometry_blocks_task_before_dispatch(self):
        self.e.backend.offset_available = False
        self.hold()
        system = self.e.observe()['system']
        place = next(n for n in system['task']['nodes'] if n['kind'] == 'place')
        self.assertEqual(place['status'], 'blocked')
        self.assertEqual(system['world']['facts']['holding_geometry:red cube']['status'], 'unknown')
        self.assertEqual([(r['fact'], r['expected']) for r in place['requirements']],
            requirements_for('place_object', dict(object='red cube', support='green cube'), self.e.snapshot))
        with self.assertRaises(DependenciesChanged):
            self.skill('place', 'place_object', dict(object='red cube', support='green cube'))
        self.assertEqual(self.e.used['calls'], 0)
        self.assertEqual(self.e.segments, {})

    def test_reported_support_availability_without_bounds_cannot_authorize_placement(self):
        self.e.backend.bounds_available = False
        self.hold()
        until(lambda: self.e.observe()['system']['world']['facts']['support_geometry:green cube']['status'] == 'unknown')
        system = self.e.observe()['system']
        self.assertEqual(system['world']['facts']['support:green cube']['status'], 'verified')
        with self.assertRaises(DependenciesChanged):
            self.skill('place', 'place_object', dict(object='red cube', support='green cube'))
        self.assertEqual(self.e.backend.moves, [])
        self.assertEqual(self.e.used['calls'], 0)

    def prepare_retreat(self):
        self.hold()
        self.e.backend.low_holding_unknown = True
        self.e.backend.xyz = [.6, 0., .08]
        self.e.backend.route = dict(status='available', target_xyz=[.6, 0., .2], target_wxyz=[0., 0., 1., 0.])
        until(lambda: self.e.observe()['system']['world']['facts']['retreat_path:red cube']['status'] == 'verified')

    def test_recorded_retreat_is_a_bound_recovery_node_without_guessing_current_holding(self):
        self.prepare_retreat()
        system = self.e.observe()['system']
        self.assertEqual(system['world']['facts']['holding:red cube']['status'], 'unknown')
        self.assertEqual(next(n for n in system['task']['nodes'] if n['kind'] == 'recover')['status'], 'ready')
        recovered = self.skill('recover', 'retreat_from_descent', dict(object='red cube'))
        self.assertEqual(recovered['execution'], 'completed', recovered.get('error'))
        self.assertEqual(recovered['skill_verification']['status'], 'verified')
        self.assertNotEqual(recovered['goal_verification']['status'], 'verified')
        self.assertEqual(recovered['task_binding']['node_id'], '1:recover')
        self.assertEqual([call['name'] for call in recovered['api_attempts']], ['goto_pose', 'check_holding'])
        self.assertEqual(self.e.backend.moves, [dict(xyz=[.6, 0., .2], quat=[0., 0., 1., 0.], approach=0.)])
        self.assertEqual(self.e.backend.held, 'red cube')
        self.assertEqual(self.e.backend.reset_count, 1)
        current = self.e.observe()['system']
        with self.assertRaises(DependenciesChanged):
            self.e.execute_skill('repeat-retreat', self.e.revision, current['state_id'], '1:recover',
                                 dict(name='retreat_from_descent', arguments=dict(object='red cube')))
        self.assertEqual(self.e.used['calls'], 2)

    def test_disappeared_retreat_authorization_rejects_old_model_choice_before_motion(self):
        self.prepare_retreat()
        before = self.e.observe()['system']
        self.e.backend.route = None
        until(lambda: self.e.observe()['system']['world']['facts']['retreat_path:red cube']['status'] == 'unknown')
        with self.assertRaises(DependenciesChanged):
            self.e.execute_skill('obsolete-retreat', self.e.revision, before['state_id'], '1:recover',
                                 dict(name='retreat_from_descent', arguments=dict(object='red cube')))
        self.assertEqual(self.e.backend.moves, [])
        self.assertEqual(self.e.used['calls'], 0)


if __name__ == '__main__':
    unittest.main()
