"""Synthetic public-state cuts for a real-model protocol probe. No physical claims."""
import argparse
import json
import tempfile
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'tests'))
from agentic_cap.core import Manager
from test_robot_system import SystemFixture
from test_runtime import until

CASES = {
    'stack_start': dict(task='cube_stack', holding=None, node='acquire', skill='acquire_object', object='red cube'),
    'restack_start': dict(task='cube_restack', holding=None, node='clear_acquire', skill='acquire_object', object='green cube'),
    'clear_held': dict(task='cube_restack', holding='green cube', node='clear', object='green cube'),
    'support_unknown': dict(task='cube_stack', holding='red cube', node='support', skill='inspect_support', object='red cube'),
}


class DecisionFixture(SystemFixture):
    def capture(self):
        state = super().capture()
        state['capabilities'] = ['inspect_support', 'inspect_placement']
        if getattr(self, 'support_hidden', False):
            state['support_evidence']['green cube'] = dict(status='unknown', reason='synthetic missing support-size evidence')
        return state


def make_case(name):
    spec = CASES[name]
    with tempfile.TemporaryDirectory() as root:
        manager = Manager(root, DecisionFixture)
        created = manager.create(name, spec['task'], 12, dict(on=['red cube', 'green cube'], empty_gripper=True))
        e = manager.episodes[created['id']]
        try:
            until(lambda: e.status == 'ready' and e.snapshot)
            with e.lock:
                e.backend.held = spec['holding']
                e.backend.support_hidden = name == 'support_unknown'
            until(lambda: e.backend.ticks >= 25)
            observation = e.observe()
            system = observation['system']
            node = next(n for n in system['task']['nodes'] if n['kind'] == spec['node'])
            if node['status'] != 'ready':
                raise RuntimeError('Synthetic case did not expose the expected ready node')
            return dict(case=name, scope='Synthetic offline decision probe; no images and no robot execution',
                expected=dict(decision='run_skill' if spec.get('skill') else 'run_code', node_id=node['id'],
                              skill=spec.get('skill'), object=spec['object']),
                context=dict(mode='execution', scenario=spec['task'],
                    user_goal='将绿色方块从红色方块上移开，再将红色方块放到绿色方块上，最后空夹爪。'
                              if spec['task'] == 'cube_restack' else '将红色方块放到绿色方块上，最后空夹爪。',
                    goal=e.goal, revision=e.revision, robot_system=system, latest_observation=observation,
                    current_subgoal='', remaining_steps=[], recent_attempts=[], used_budget=e.state()['used'],
                    remaining_wall_milliseconds=90000, limits=e.limits,
                    uncertainty='Missing support-size evidence remains unknown; use the Runtime task requirements.'))
        finally:
            e.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('case', choices=CASES)
    args = parser.parse_args()
    print(json.dumps(make_case(args.case)))
