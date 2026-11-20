"""Fixed short-program task trials on the real runtime, with private scoring.

These measure adapter/closed-loop feasibility, not an LLM policy success rate.
"""
import argparse
import json
import time
from pathlib import Path
from agentic_cap.core import Manager
from agentic_cap.cap_backend import CaPBackend


def wait(fn, seconds=180):
    end = time.monotonic()+seconds
    while time.monotonic() < end:
        if fn():
            return
        time.sleep(.1)
    raise TimeoutError('Runtime did not complete')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--upstream', required=True)
    parser.add_argument('--records', required=True)
    args = parser.parse_args()
    root = Path(args.records).resolve()
    manager = Manager(root, lambda e: CaPBackend(e, args.upstream, evaluation=True))
    results = []
    for task in ['cube_lifting', 'cube_stack', 'cube_restack']:
        goal = {'holding': 'red cube'} if task == 'cube_lifting' else {'on': ['red cube', 'green cube'], 'empty_gripper': True}
        result = dict(task=task, policy='fixed Python segments; not LLM', segments=[])
        state = manager.create(task, task, 0, goal)
        e = manager.episodes[state['id']]
        result['episode_id'] = e.id
        def segment(label, code):
            sid = e.execute(label, e.revision, label, code)
            wait(lambda: not e.active, 90)
            data = e.segments[sid]
            result['segments'].append(data.copy())
            print(json.dumps(dict(task=task, phase=label, execution=data['execution'], error=data.get('error'))), flush=True)
            return data['execution'] == 'completed'

        def pick(name):
            if not segment('grasp-'+name, 'open_gripper()\np, q = sample_grasp_pose('+repr(name)+')\ngoto_pose(p, q, z_approach=0.1)\nclose_gripper()'):
                return False
            return segment('lift-'+name, 's = get_execution_state()\np = s["tcp_xyz"]\nq = s["tcp_wxyz"]\np[2] = p[2]+0.16\ngoto_pose(p,q)\ncheck_holding('+repr(name)+')')

        def place(name, support, aside=False):
            code = '''s = get_execution_state()
p, unused, extent = get_object_pose(SUPPORT)
q = s["tcp_wxyz"]
obj = s["objects"][OBJECT]["surface_median_xyz"]
tcp = s["tcp_xyz"]
p[0] = p[0]-(obj[0]-tcp[0])+OFFSET
p[1] = p[1]-(obj[1]-tcp[1])
p[2] = p[2]-(obj[2]-tcp[2])+HEIGHT
goto_pose(p,q,z_approach=0.10)
open_gripper()
'''.replace('SUPPORT', repr(support)).replace('OBJECT', repr(name)).replace('OFFSET', '-0.15' if aside else '0.0').replace('HEIGHT', '0.0' if aside else '0.045')
            if not segment('release-'+name, code):
                return False
            return segment('retreat-'+name, 's=get_execution_state()\np=s["tcp_xyz"]\np[2]=p[2]+0.10\ngoto_pose(p,s["tcp_wxyz"])')

        try:
            wait(lambda: e.snapshot or e.status == 'failed')
            if e.status == 'failed':
                raise RuntimeError(str(e.events[-1]))
            clear = True
            if task == 'cube_restack':
                clear = pick('green cube') and place('green cube', 'red cube', aside=True)
            if clear and pick('red cube') and task != 'cube_lifting':
                place('red cube', 'green cube')
            time.sleep(1.5)
            result['verification'] = e.finish(e.revision)
            result['used'] = e.state()['used']
        except Exception as exc:
            result['error'] = str(exc)
        finally:
            e.close()
        results.append(result)
        (root / 'task-report.json').write_text(json.dumps(results, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
