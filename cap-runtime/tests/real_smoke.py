"""Real CaP smoke acceptance. Fixed programs, never labelled LLM success rate."""
import argparse
import json
import time
from pathlib import Path

from agentic_cap.core import Manager
from agentic_cap.cap_backend import CaPBackend


def until(fn, seconds=180):
    end = time.monotonic()+seconds
    while time.monotonic() < end:
        result = fn()
        if result:
            return result
        time.sleep(.1)
    raise TimeoutError('Condition did not complete')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--upstream', required=True)
    parser.add_argument('--records', required=True)
    args = parser.parse_args()
    records = Path(args.records).resolve()
    manager = Manager(records, lambda e: CaPBackend(e, args.upstream))
    rows = []
    for task in ['cube_lifting', 'cube_stack', 'cube_restack']:
        goal = {'holding': 'red cube'} if task == 'cube_lifting' else {'on': ['red cube', 'green cube'], 'empty_gripper': True}
        state = manager.create(task, task, 0, goal)
        e = manager.episodes[state['id']]
        row = dict(task=task, episode_id=e.id)
        try:
            until(lambda: e.snapshot or e.status == 'failed')
            if e.status == 'failed':
                raise RuntimeError(str(e.events[-1]))
            t = time.monotonic()
            before = e.observe()['simulation_time']
            time.sleep(5)
            advance = e.observe()['simulation_time'] - before
            row.update(model_delay_seconds=time.monotonic()-t, simulation_advance_seconds=advance,
                       real_time_factor=advance/(time.monotonic()-t), continuous_pass=advance >= 4.5)
            # Safe motion/cancellation through the actual upstream API.
            sid = e.execute('motion', 1, 'home', 'home_pose()\nclose_gripper()\nopen_gripper()')
            time.sleep(.25)
            row['stop'] = e.cancel()
            until(lambda: e.active is None, 15)
            row['cancelled_segment'] = e.segments[sid]
            # Empty grasp is an intentional failure: it must not mark the goal complete.
            sid = e.execute('miss', 1, 'empty grasp evidence', 'close_gripper()\ncheck_holding("red cube")')
            until(lambda: e.active is None, 30)
            row['failure_segment'] = e.segments[sid]
            sid = e.execute('recover', 1, 'open and reobserve', 'open_gripper()\ns = get_execution_state()')
            until(lambda: e.active is None, 20)
            row['recovery_segment'] = e.segments[sid]
            row['reset_count'] = 1
            row['final_verification'] = e.finish(1)
            row['used'] = e.state()['used']
        except Exception as exc:
            row['error'] = str(exc)
        finally:
            e.close()
        rows.append(row)
        (records / 'smoke-report.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
        print(json.dumps(row), flush=True)


if __name__ == '__main__':
    main()
