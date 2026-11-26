"""Regression of the observed place -> rejected release -> retreat -> repeat cycle."""
import copy
import unittest
from agentic_cap.placement_retry import placement_context, retry_constraint
from agentic_cap.task_recipes import build_task
from agentic_cap.robot_system import TaskExecutive

GOAL = dict(on=['red cube', 'green cube'], empty_gripper=True)
ARGS = dict(object='red cube', support='green cube')


def scene():
    snapshot = dict(capabilities=['inspect_support', 'inspect_placement'],
        objects={'green cube': dict(status='visible', surface_q05_xyz=[.4, .1, -.1], surface_q95_xyz=[.44, .14, -.06]),
                 'red cube': dict(status='visible', surface_median_xyz=[.5, 0, .2])},
        support_evidence={'green cube': dict(status='available')})
    facts = {key: dict(status='verified') for key in ['holding:red cube', 'holding_geometry:red cube',
             'support:green cube', 'support_geometry:green cube', 'extent:red cube']}
    facts.update({'goal': dict(status='unknown'), 'empty_gripper': dict(status='failed'),
                  'on:green cube:red cube': dict(status='failed'), 'retreat_path:red cube': dict(status='unknown')})
    facts['holding_geometry:red cube']['result'] = dict(geometry=dict(offset_xyz=[0, 0, 0], tcp_wxyz=[0, 0, 1, 0]))
    return snapshot, facts


def failed_place(snapshot, facts):
    context = placement_context(snapshot, facts, ARGS)
    return dict(id='blocked-release', execution='failed', effect='unknown',
        task_binding=dict(node_id='1:place', goal_version=1, retry_context=context),
        feedback=dict(category='insufficient_evidence', api='open_gripper', evidence=dict(evidence=dict(
            status='unknown', object_name='green cube', reason='support_horizontal_extent_truncated', evidence_ref='low-frame'))))


def place(snapshot, facts, segments, version=1):
    _, nodes = build_task(GOAL, facts, snapshot, segments, version)
    return next(n for n in nodes if n['kind']=='place'), nodes


class PlacementRetryTests(unittest.TestCase):
    def test_first_placement_remains_ready_and_admission_freezes_public_context(self):
        snapshot, facts = scene()
        node, _ = place(snapshot, facts, {})
        self.assertEqual(node['status'], 'ready')
        binding = TaskExecutive.admit(dict(world=dict(fresh=True), executor={}, goal_version=1,
            state_id='fresh', task=dict(execution=dict(phase='ready'))), node)
        snapshot['objects']['green cube']['surface_q05_xyz'][0] += .1
        self.assertEqual(binding['retry_context']['support_min'][0], .4)

    def test_successful_retreat_does_not_reauthorize_same_failed_release_route(self):
        snapshot, facts = scene()
        failed = failed_place(snapshot, facts)
        retreat = dict(id='retreat', execution='completed', task_binding=dict(node_id='1:recover', goal_version=1),
            node_verification=dict(status='verified'), task_outcome=dict(phase='verified', next_action='continue_task'))
        snapshot['objects']['red cube']['surface_median_xyz'] = [.42, .12, .3]
        snapshot.update(evidence_ref='new-frame', captured_at=999, control_step=999)
        facts['holding_geometry:red cube']['result']['geometry']['offset_xyz'][0] += .006
        node, nodes = place(snapshot, facts, {'place':failed, 'retreat':retreat})
        self.assertEqual(node['status'], 'blocked')
        self.assertEqual(node['retry_constraint']['segment_id'], 'blocked-release')
        system = dict(world=dict(fresh=True), executor={}, task=dict(execution=dict(phase='ready')))
        with self.assertRaises(ValueError):
            TaskExecutive.admit(system, node)
        projection = TaskExecutive.project(nodes, facts, {'place':failed,'retreat':retreat}, goal_version=1,
            active=None, status='ready', fresh=True, hold_pending=False)
        self.assertEqual(projection['phase'], 'placement_route_blocked')
        self.assertEqual(projection['ready_nodes'], [])

    def test_authorized_retreat_is_still_ready_while_place_is_blocked(self):
        snapshot, facts = scene()
        failed = failed_place(snapshot, facts)
        facts['retreat_path:red cube']['status'] = 'verified'
        _, nodes = place(snapshot, facts, {'place':failed})
        self.assertEqual(next(n for n in nodes if n['kind']=='recover')['status'], 'ready')
        projection = TaskExecutive.project(nodes, facts, {'place':failed}, goal_version=1,
            active=None, status='ready', fresh=True, hold_pending=False)
        self.assertEqual(projection['phase'], 'recovery_ready')
        self.assertIn('1:recover', projection['ready_nodes'])

    def test_material_geometry_grasp_or_camera_change_permits_fresh_runtime_checks(self):
        for change in ['support', 'grasp', 'orientation', 'camera']:
            with self.subTest(change=change):
                snapshot, facts = scene(); failed=failed_place(snapshot, facts)
                if change=='support': snapshot['objects']['green cube']['surface_q05_xyz'][0] += .04
                if change=='grasp': failed['task_binding']['retry_context']['grasp_attempt']='previous-grasp'
                if change=='orientation': facts['holding_geometry:red cube']['result']['geometry']['tcp_wxyz']=[0, 1, 0, 0]
                if change=='camera': snapshot['camera_views']={'robot0_eye_in_hand':{}}
                self.assertEqual(place(snapshot, facts, {'place':failed})[0]['status'], 'ready')

    def test_unrelated_failures_do_not_constrain_placement(self):
        snapshot, facts = scene()
        for category, api, execution in [('invalid_program','open_gripper','failed'),
                ('insufficient_evidence','goto_pose','failed'), ('insufficient_evidence','open_gripper','cancelled')]:
            failed=failed_place(snapshot, facts)
            failed['feedback'].update(category=category, api=api); failed['execution']=execution
            self.assertEqual(place(snapshot, facts, {'place':failed})[0]['status'], 'ready')

    def test_new_goal_fences_old_failure_and_legacy_missing_context_does_not_invent_evidence(self):
        snapshot, facts = scene(); failed=failed_place(snapshot, facts)
        self.assertEqual(place(snapshot, facts, {'place':failed}, version=2)[0]['status'], 'ready')
        failed['task_binding'].pop('retry_context')
        self.assertIsNone(retry_constraint([failed], placement_context(snapshot, facts, ARGS), 'green cube'))

    def test_release_and_verified_goal_keep_their_existing_verification_policy(self):
        snapshot, facts=scene(); failed=failed_place(snapshot, facts)
        snapshot['bounded_recovery_state']=dict(placement_session=dict(current_goal=True, release_id='release'))
        self.assertEqual(place(snapshot, facts, {'place':failed})[0]['status'], 'awaiting_verification')
        facts['goal']['status']='verified'
        self.assertEqual(place(snapshot, facts, {'place':failed})[0]['status'], 'not_required')

    def test_unknown_or_missing_geometry_does_not_become_placement_permission(self):
        snapshot, facts=scene(); failed=failed_place(snapshot, facts)
        snapshot['objects']['green cube'].pop('surface_q95_xyz')
        facts['support_geometry:green cube']['status']='unknown'
        self.assertEqual(place(snapshot, facts, {'place':failed})[0]['status'], 'blocked')
        self.assertIsNone(placement_context(snapshot, facts, ARGS))

    def test_not_holding_has_explicit_null_geometry_and_remains_observable(self):
        snapshot, facts = scene()
        facts['holding_geometry:red cube'] = dict(status='unknown', result=dict(geometry=None))
        self.assertIsNone(placement_context(snapshot, facts, ARGS))
        self.assertEqual(place(snapshot, facts, {})[0]['status'], 'blocked')

    def test_same_grasp_estimation_drift_and_partial_edge_recovery_are_not_a_repair(self):
        snapshot, facts=scene(); failed=failed_place(snapshot, facts)
        facts['holding_geometry:red cube']['result']['geometry']['offset_xyz'][0] -= .012
        snapshot['objects']['green cube']['surface_q05_xyz'][0] -= .011
        self.assertEqual(place(snapshot, facts, {'place':failed})[0]['status'], 'blocked')

    def test_quaternion_sign_and_frame_changes_are_not_a_new_route(self):
        snapshot, facts=scene(); failed=failed_place(snapshot, facts)
        facts['holding_geometry:red cube']['result']['geometry']['tcp_wxyz']=[0, 0, -1, 0]
        self.assertEqual(place(snapshot, facts, {'place':failed})[0]['status'], 'blocked')


if __name__=='__main__': unittest.main()
