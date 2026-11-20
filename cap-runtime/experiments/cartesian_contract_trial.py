"""Frozen public-driver task and delayed-IK control regression; no LLM or truth input."""
import argparse
import copy
import json
import os
import threading
import time
from pathlib import Path

from agentic_cap.cap_backend import CaPBackend
from agentic_cap.cartesian_motion import MOTION_PROTOCOL
from agentic_cap.core import Manager, StaleRevision
from agentic_cap.server import runtime_provenance
from experiments.active_view_trial import wait_for
from experiments.runtime_support_trial import RuntimeSupportTrial
from experiments.vertical_retreat_trial import source_hashes


CASES = ['cube_lifting', 'cube_stack', 'cube_restack', 'unreachable', 'cancel_ik', 'change_goal_ik']


class DelayedIKBackend(CaPBackend):
    def initialize(self):
        super().initialize()
        original = self.api.ik_solve_fn
        self.entered = threading.Event()
        self.release = threading.Event()
        self.delayed_once = False
        def delayed(**kwargs):
            result = original(**kwargs)
            if not self.delayed_once:
                self.delayed_once = True
                self.episode.event('diagnostic_ik_reply_delayed')
                self.entered.set()
                if not self.release.wait(20):
                    raise TimeoutError('Diagnostic IK reply not released')
                self.episode.event('diagnostic_ik_reply_released')
            return result
        self.api.ik_solve_fn = delayed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--upstream', required=True)
    parser.add_argument('--records', type=Path, required=True)
    args = parser.parse_args()
    root = args.records.resolve()
    root.mkdir(parents=True, exist_ok=False)
    os.environ.update(CAP_ACTIVE_EVIDENCE='1', CAP_PLACEMENT_EVIDENCE='1',
        CAP_HOLDING_WATCHDOG='1', CAP_SUPPORT_SECOND_VIEW='robot0_eye_in_hand', CAP_SUPPORT_OCCLUSION='')
    hashes = source_hashes()
    (root/'protocol.json').write_text(json.dumps(dict(cases=CASES, seed=81,
        protocol=MOTION_PROTOCOL, runtime=runtime_provenance('cap', args.upstream), source_sha256=hashes,
        settle_seconds=1.5, scope='Fixed public geometry driver and synthetic delayed-IK controls; no LLM or success-rate comparison'), indent=2))
    rows = []
    for mode in CASES:
        if source_hashes() != hashes:
            raise RuntimeError('Frozen source changed')
        delayed = mode in {'cancel_ik', 'change_goal_ik'}
        manager = Manager(root, lambda e: (DelayedIKBackend if delayed else CaPBackend)(e, args.upstream, evaluation=True))
        task = mode if mode.startswith('cube_') else 'cube_lifting'
        goal = dict(holding='red cube') if task == 'cube_lifting' else dict(on=['red cube', 'green cube'], empty_gripper=True)
        state = manager.create(mode, task, 81, goal)
        e = manager.episodes[state['id']]
        trial = RuntimeSupportTrial(e, 'support_active')
        row = dict(mode=mode, episode_id=e.id, task=task, passed=False)
        try:
            wait_for(lambda:e.snapshot or e.status=='failed',150)
            if e.status!='ready':
                raise RuntimeError('Initialization failed')
            if delayed:
                prior = e.revision
                s = e.observe()
                e.decision(mode, prior)
                sid = e.execute(mode, prior, mode,
                    f'goto_pose({s["tcp_xyz"]!r}, {s["tcp_wxyz"]!r})\nopen_gripper()')
                wait_for(lambda:e.backend.entered.is_set(),30)
                first = e.events[-1]['id']
                before_time = e.observe()['simulation_time']
                if mode=='cancel_ik':
                    row['control_result'] = e.cancel()
                else:
                    e.update_goal(dict(holding='green cube'))
                    row['control_result'] = dict(stopped=e.hold_ack>=e.hold_serial)
                e.backend.release.set()
                wait_for(lambda:e.active is None,15)
                time.sleep(.5)
                row['segment'] = copy.deepcopy(e.segments[sid])
                row['late_cartesian_events'] = [ev['id'] for ev in e.events if ev['id']>first
                    and ev['kind'] in {'cartesian_ik_checked','cartesian_motion_finished'}]
                row['late_api_starts'] = [ev['id'] for ev in e.events if ev['id']>first and ev['kind']=='api_started']
                row['simulation_advance_after_interrupt'] = e.observe()['simulation_time']-before_time
                if mode=='change_goal_ik':
                    try:
                        e.execute('late-old-request',prior,'late','open_gripper()')
                    except StaleRevision:
                        row['old_revision_rejected'] = True
                    else:
                        raise RuntimeError('Old revision accepted')
                row['passed'] = bool(row['control_result']['stopped'] and row['segment']['execution']=='cancelled'
                                     and not row['late_cartesian_events'] and not row['late_api_starts']
                                     and e.used['motion_steps']==0)
            elif mode=='unreachable':
                e.decision(mode,e.revision)
                sid = e.execute(mode,e.revision,mode,'goto_pose([2.,0.,.1],[0,0,1,0])\nopen_gripper()')
                wait_for(lambda:e.active is None,45)
                row['segment'] = copy.deepcopy(e.segments[sid])
                row['ik_attempts'] = sum(ev['kind']=='cartesian_ik_checked' for ev in e.events)
                row['passed'] = bool(row['segment']['execution']=='failed'
                    and row['segment']['feedback']['category']=='motion_target_not_reached'
                    and row['ik_attempts']==MOTION_PROTOCOL['max_ik_attempts']
                    and e.used['calls']==1 and e.used['motion_steps']==0)
            else:
                e.decision('initial',e.revision)
                trial.measure('initial')
                if task=='cube_restack':
                    measurement = trial.pick('green cube')
                    trial.place(measurement,'green cube','red cube',aside=True)
                measurement = trial.pick('red cube')
                if task!='cube_lifting':
                    measurement = trial.prepare_placement(measurement,'red cube','green cube')
                    trial.place(measurement,'red cube','green cube')
                e.decision('finish',e.revision)
                row['public_final'] = e.finish(e.revision)
                row['passed'] = row['public_final']['status']=='verified'
            time.sleep(1.5)
        except Exception as exc:
            row['error'] = str(exc)
        finally:
            if delayed and getattr(e.backend,'release',None):
                e.backend.release.set()
            row['public_at_close'] = e.verify()
            e.close()
            row.update(closed=e.state()['status'], used=e.state()['used'],
                       segments=copy.deepcopy(trial.segments), source_unchanged=source_hashes()==hashes)
            rows.append(row)
            (root/'results.json').write_text(json.dumps(rows,indent=2))
            print(json.dumps({k:row.get(k) for k in ['mode','episode_id','passed','error','used']}),flush=True)
    if not all(r['passed'] and r['source_unchanged'] for r in rows):
        raise SystemExit(1)


if __name__=='__main__':
    main()
