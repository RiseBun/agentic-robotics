"""One real CaP restack smoke run for the bounded inspect_support skill.

Engineering-only: fixed Python segments, no LLM and no private truth in the
controller. The private evaluator is used only after the episode closes.
"""
import argparse
import json
import os
import time
from pathlib import Path

from agentic_cap.cap_backend import CaPBackend
from agentic_cap.core import Manager


def wait(predicate, timeout=180):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.1)
    raise TimeoutError("Runtime did not complete")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--upstream', required=True)
    parser.add_argument('--records', required=True)
    parser.add_argument('--seed', type=int, default=22)
    parser.add_argument('--support-occlusion', action='store_true',
                        help='Hide public green-support pixels until lateral inspection')
    args = parser.parse_args()
    os.environ['CAP_ACTIVE_EVIDENCE'] = '1'
    if args.support_occlusion:
        os.environ['CAP_SUPPORT_OCCLUSION'] = 'green_until_lateral_view'
    root = Path(args.records).resolve()
    manager = Manager(root, lambda e: CaPBackend(e, args.upstream, evaluation=True))
    goal = {'on': ['red cube', 'green cube'], 'empty_gripper': True}
    state = manager.create('active-evidence-restack', 'cube_restack', args.seed, goal)
    episode = manager.episodes[state['id']]
    result = {'episode_id': episode.id, 'seed': args.seed,
              'support_occlusion': args.support_occlusion, 'segments': []}
    wait(lambda: episode.snapshot or episode.status in {'failed', 'budget_exhausted'}, 180)
    if episode.status != 'ready':
        raise RuntimeError('Episode failed before ready: ' + episode.status)

    def segment(label, code, contract=None):
        segment_id = episode.execute(label, episode.revision, label, code, contract)
        wait(lambda: episode.active is None, 180)
        row = episode.segments[segment_id].copy()
        result['segments'].append(row)
        return row

    def pick(name):
        first = segment('grasp-' + name,
            'open_gripper()\np, q = sample_grasp_pose(' + repr(name) + ')\n'
            'goto_pose(p, q, z_approach=0.1)\nclose_gripper()')
        if first['execution'] != 'completed':
            return False
        second = segment('lift-' + name,
            's=get_execution_state()\np=s["tcp_xyz"]\nq=s["tcp_wxyz"]\n'
            'p[2]=p[2]+0.12\ngoto_pose(p,q)\ncheck_holding(' + repr(name) + ')',
            dict(postcondition={'holding': name}))
        return second['execution'] == 'completed' and second.get('stage_verification', {}).get('status') == 'verified'

    def place(name, support, aside=False):
        code = '''s=get_execution_state()
p, unused, extent = get_object_pose(SUPPORT)
q=s["tcp_wxyz"]
obj=s["objects"][OBJECT]["surface_median_xyz"]
tcp=s["tcp_xyz"]
p[0]=p[0]-(obj[0]-tcp[0])+OFFSET
p[1]=p[1]-(obj[1]-tcp[1])
p[2]=p[2]-(obj[2]-tcp[2])+HEIGHT
goto_pose(p,q,z_approach=0.10)
open_gripper()
'''.replace('SUPPORT', repr(support)).replace('OBJECT', repr(name)).replace(
            'OFFSET', '-0.15' if aside else '0.0').replace('HEIGHT', '0.0' if aside else '0.045')
        row = segment('release-' + name, code)
        return row['execution'] == 'completed'

    try:
        cleared = pick('green cube') and place('green cube', 'red cube', aside=True)
        held = cleared and pick('red cube')
        inspected = None
        if held:
            inspected = segment('inspect-support', 'inspect_support("green cube", "red cube")')
            result['inspection'] = inspected.get('evidence_acquisition')
            if inspected['execution'] == 'completed' and result['inspection']['status'] == 'available':
                place('red cube', 'green cube')
        wait(lambda: not episode.active, 30)
        result['verification'] = episode.finish(episode.revision)
        result['used'] = episode.state()['used']
    except Exception as exc:
        result['error'] = str(exc)
    finally:
        episode.close()
    (root / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
