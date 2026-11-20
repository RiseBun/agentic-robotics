"""Optional private scorer. Never called by an API worker or sent to the model."""
import json
import time


class PrivateEvaluator:
    def __init__(self, low, directory):
        self.low = low
        self.file = directory / 'evaluation-private.jsonl'
        self.base_heights = {}
        self.last_write = 0

    def sample(self, episode):
        import numpy as np
        rs, sim = self.low.robosuite_env, self.low.robosuite_env.sim
        bodies = {'red cube': getattr(rs, 'cubeA_body_id', getattr(rs, 'cube_body_id', None)),
                  'green cube': getattr(rs, 'cubeB_body_id', None)}
        positions = {name: np.array(sim.data.body_xpos[body], copy=True) for name, body in bodies.items() if body is not None}
        for name, point in positions.items():
            self.base_heights.setdefault(name, float(point[2]))
        contacts = {name: set() for name in positions}
        pairs = set()
        for contact in sim.data.contact[:sim.data.ncon]:
            a, b = int(contact.geom1), int(contact.geom2)
            body_a, body_b = int(sim.model.geom_bodyid[a]), int(sim.model.geom_bodyid[b])
            pairs.add(frozenset([body_a, body_b]))
            for name, body in bodies.items():
                if body is None:
                    continue
                other = b if body_a == body else a if body_b == body else None
                if other is not None:
                    geom = sim.model.geom_id2name(other) or ''
                    contacts[name].update(i for i in [1, 2] if f'finger{i}_' in geom)
        results = []
        goal = episode.goal
        if goal.get('holding'):
            name = goal['holding']
            results.append(name in positions and len(contacts.get(name, [])) == 2 and positions[name][2] - self.base_heights[name] >= .04)
        if goal.get('on'):
            a, b = goal['on']
            if b == 'table':
                # Table body identity varies across versions; do not fabricate a label.
                results.append(None)
            elif a not in positions or b not in positions:
                results.append(False)
            else:
                delta = positions[a] - positions[b]
                results.append(np.linalg.norm(delta[:2]) <= .035 and 0 < delta[2] <= .065 and frozenset([bodies[a], bodies[b]]) in pairs)
        if goal.get('empty_gripper'):
            results.append(not any(len(fingers) == 2 for fingers in contacts.values()))
        success = None if None in results else bool(results and all(results))
        if time.monotonic()-self.last_write >= .5:
            self.last_write = time.monotonic()
            row = dict(time=time.time(), episode_id=episode.id, segment_id=episode.active,
                       revision=episode.revision, goal=goal, task_completed=success,
                       coordinate_convention=dict(positions_frame='world', length_unit='m',
                                                  quaternion_order='WXYZ'),
                       world_from_cap_robot_base_xyz_wxyz=np.concatenate([
                           np.array(sim.data.body_xpos[self.low.base_link_idx], copy=True),
                           np.array(sim.data.body_xquat[self.low.base_link_idx], copy=True)]).tolist(),
                       world_positions={name: point.tolist() for name, point in positions.items()},
                       bilateral_contact={name: len(fingers) == 2 for name, fingers in contacts.items()},
                       scope='private scorer only; never policy context')
            with self.file.open('a') as stream:
                stream.write(json.dumps(row) + '\n')
