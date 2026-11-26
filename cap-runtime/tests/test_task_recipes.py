"""Recipe semantics and authority; fixture results are not physical evidence."""
import copy
import unittest

from agentic_cap.robot_system import TaskExecutive
from agentic_cap.task_recipes import RECIPES, build_task, select_recipe, validate_recipe


class RecipeTests(unittest.TestCase):
    def facts(self, **overrides):
        values = {'goal': 'failed', 'empty_gripper': 'verified',
                  'holding:red cube': 'failed', 'holding:green cube': 'failed',
                  'on:red cube:green cube': 'failed', 'on:green cube:red cube': 'failed',
                  'support:red cube': 'verified', 'support:green cube': 'verified',
                  'retreat_path:red cube': 'unknown', 'retreat_path:green cube': 'unknown',
                  'release_recorded': 'unknown'}
        values.update(overrides)
        return {k: dict(status=v) for k, v in values.items()}

    def test_registered_recipes_are_acyclic_and_do_not_modify_shared_specs(self):
        before = copy.deepcopy(RECIPES)
        for specs in RECIPES.values():
            validate_recipe(specs)
        for obj, support in [('red cube', 'green cube'), ('green cube', 'red cube')]:
            recipe, nodes = build_task(dict(on=[obj, support], empty_gripper=True), self.facts(),
                                       dict(backend='fixture'), {}, 7)
            acquire = next(n for n in nodes if n['kind'] == 'acquire')
            blocker = next(n for n in nodes if n['kind'] == 'clear_acquire')
            self.assertEqual(acquire['arguments'], dict(object=obj))
            self.assertEqual(blocker['arguments'], dict(object=support))
            self.assertTrue(all(n['id'].startswith('7:') for n in nodes))
            self.assertEqual(recipe['authority'], 'Runtime')
        self.assertEqual(RECIPES, before)

    def test_broken_recipes_fail_before_becoming_dispatchable(self):
        valid = copy.deepcopy(RECIPES['hold_object_v1'])
        cases = [valid+[valid[0]], [dict(valid[0], depends_on=['verify']), valid[1]],
                 [dict(valid[0], depends_on=['missing']), valid[1]],
                 [dict(valid[0], skill='shell'), valid[1]], [dict(valid[0], effects=[])]]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                validate_recipe(case)

    def test_accepted_predicate_without_action_recipe_does_not_grant_cap(self):
        for goal in [dict(on=['red cube', 'table']), dict(empty_gripper=True),
                     dict(on=['red cube', 'table'], holding='red cube')]:
            self.assertEqual(select_recipe(goal)[0], 'verify_existing_goal_v1')
            recipe, nodes = build_task(goal, self.facts(), dict(backend='fixture'), {}, 1)
            self.assertEqual(recipe['action_coverage'], 'verification_only')
            system = dict(world=dict(fresh=True), executor={}, task=dict(execution=dict(phase='blocked')))
            with self.assertRaises(ValueError):
                TaskExecutive.admit(system, nodes[0])

    def test_missing_facts_never_satisfy_negative_clearance_requirement(self):
        recipe, nodes = build_task(dict(on=['red cube', 'green cube']), {}, dict(backend='fixture'), {}, 1)
        self.assertTrue(all(n['status'] != 'ready' for n in nodes if n['kind'] != 'verify'))
        self.assertEqual(next(n for n in nodes if n['kind'] == 'acquire')['status'], 'blocked')

    def test_release_record_routes_back_to_verification_not_regrasp(self):
        snapshot = dict(backend='fixture', capabilities=['inspect_placement'],
                        bounded_recovery_state=dict(placement_session=dict(current_goal=True, release_id='release-1')))
        recipe, nodes = build_task(dict(on=['red cube', 'green cube']),
            self.facts(release_recorded='verified'), snapshot, {}, 1)
        for node in nodes:
            if node['kind'] in {'acquire', 'support', 'place'}:
                self.assertEqual(node['status'], 'awaiting_verification')
        self.assertEqual(next(n for n in nodes if n['kind'] == 'verify')['skills'], ['inspect_placement'])

    def test_recovery_authorization_is_not_inferred_from_holding(self):
        _, nodes = build_task(dict(on=['red cube', 'green cube']),
            self.facts(**{'holding:red cube': 'verified'}), dict(backend='fixture'), {}, 1)
        self.assertEqual(next(n for n in nodes if n['kind'] == 'recover')['status'], 'not_required')

    def test_monitor_and_interface_cancellation_keep_their_recovery_category(self):
        for category, phase in [('public_holding_loss', 'holding_lost'),
                                ('episode_unavailable', 'interface_unavailable'),
                                ('cancelled', 'cancelled'), ('budget_exhausted', 'budget_exhausted')]:
            segment = dict(execution='cancelled', feedback=dict(category=category))
            self.assertEqual(TaskExecutive.outcome(segment)['phase'], phase)


if __name__ == '__main__':
    unittest.main()
