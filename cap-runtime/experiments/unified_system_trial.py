"""Frozen architecture diagnosis with real CaP; deterministic skill selection.

Uses development seed 12 once per scenario. No LLM and no success-rate claim.
Private physical scoring is a separate process after every episode is closed.
"""
import argparse
import hashlib
import json
import os
import time
from pathlib import Path

from agentic_cap.cap_backend import CaPBackend
from agentic_cap.core import Manager
from agentic_cap.server import runtime_provenance
from experiments.active_view_trial import wait_for
from experiments.runtime_support_trial import RuntimeSupportTrial


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--upstream', required=True)
    parser.add_argument('--records', required=True, type=Path)
    parser.add_argument('--recover-once', action='store_true', help='Probe one registered retreat after a failed placement; do not retry release')
    parser.add_argument('--tasks', nargs='+', choices=['cube_lifting','cube_stack','cube_restack'], default=['cube_lifting','cube_stack','cube_restack'])
    parser.add_argument('--seed', type=int, default=12)
    parser.add_argument('--alternate-recovery', action='store_true', help='Probe bounded yaw alternative after an authorized retreat; keep original budgets')
    args = parser.parse_args()
    if len(set(args.tasks)) != len(args.tasks):
        parser.error('Duplicate tasks are not a frozen trial plan')
    args.records.mkdir(parents=True, exist_ok=False)
    os.environ.update(CAP_ACTIVE_EVIDENCE='1', CAP_PLACEMENT_EVIDENCE='1',
                      CAP_HOLDING_WATCHDOG='1', CAP_SUPPORT_OCCLUSION='', CAP_ALTERNATE_PLACEMENT='1' if args.alternate_recovery else '0')
    protocol = dict(seed=args.seed, tasks=args.tasks,
        controller_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        secondary_camera=os.environ.get('CAP_SUPPORT_SECOND_VIEW',''),
        controller='deterministic registered-skill selection; existing restricted CaP clearing for restack',
        attempts_per_scenario=1, model=None, thinking_delay_seconds=5., settle_seconds=1.5,
        registered_recovery_probe=args.recover_once, alternate_recovery=args.alternate_recovery,
        scope='development-seed architecture diagnosis; not LLM competence or a benchmark',
        runtime=runtime_provenance('cap', args.upstream))
    (args.records/'protocol.json').write_text(json.dumps(protocol, indent=2))
    manager = Manager(args.records, lambda e: CaPBackend(e, args.upstream, evaluation=True))
    rows = []
    for task in protocol['tasks']:
        goal = dict(holding='red cube') if task == 'cube_lifting' else dict(on=['red cube', 'green cube'], empty_gripper=True)
        created = manager.create(task, task, protocol['seed'], goal)
        e = manager.episodes[created['id']]
        row = dict(task=task, episode_id=e.id, skills=[])

        def run(kind, name, arguments):
            state = e.observe()['system']
            node = next(n for n in state['task']['nodes'] if n['kind'] == kind)
            window_deadline=time.monotonic()+3
            while node['status']=='blocked' and any(r['actual']=='unknown' for r in node['requirements']) and time.monotonic()<window_deadline:
                time.sleep(.05)
                state=e.observe()['system']
                node=next(n for n in state['task']['nodes'] if n['kind']==kind)
            if node['status'] != 'ready':
                raise RuntimeError('Node not ready: '+json.dumps(node))
            key = f'{len(row["skills"])}-{name}'
            e.decision(key, e.revision)
            identity = e.execute_skill(key, e.revision, state['state_id'], node['id'],
                                       dict(name=name, arguments=arguments))
            wait_for(lambda: e.active is None, 95)
            segment = e.segments[identity]
            result = dict(segment_id=identity, skill=name, execution=segment['execution'],
                          verification=segment.get('skill_verification'), error=segment.get('error'))
            row['skills'].append(result)
            print(json.dumps(dict(task=task, **result)), flush=True)
            if segment['execution'] != 'completed':
                raise RuntimeError('Skill did not complete: '+str(segment.get('error')))
            return segment

        try:
            wait_for(lambda: e.snapshot or e.status == 'failed', 150)
            if e.status != 'ready':
                raise RuntimeError('Episode initialization failed')
            time.sleep(1.)
            before, start = e.observe(), time.monotonic()
            time.sleep(protocol['thinking_delay_seconds'])
            after, elapsed = e.observe(), time.monotonic()-start
            row['continuous_control'] = dict(wall_seconds=elapsed,
                simulation_seconds=after['simulation_time']-before['simulation_time'],
                control_steps=after['control_step']-before['control_step'])
            row['continuous_control']['real_time_factor'] = row['continuous_control']['simulation_seconds']/elapsed
            if task == 'cube_restack':
                # This deliberately remains an explicitly recorded CaP fallback,
                # not a claim that obstruction clearing is a registered skill.
                trial = RuntimeSupportTrial(e, 'support_active')
                trial.measure('initial')
                held = trial.pick('green cube')
                trial.place(held, 'green cube', 'red cube', aside=True)
                row['clearing_route'] = 'existing_CaP_fallback'
            run('acquire', 'acquire_object', dict(object='red cube'))
            if task != 'cube_lifting':
                state = e.observe()['system']
                if state['world']['facts']['support:green cube']['status'] != 'verified':
                    run('support', 'inspect_support', dict(object='red cube', support='green cube'))
                for placement_attempt in range(3 if args.alternate_recovery else 1):
                    try:
                        run('place','place_object',dict(object='red cube',support='green cube'))
                        break
                    except RuntimeError as exc:
                        if args.recover_once or args.alternate_recovery:
                            state=e.observe()['system']
                            node=next((n for n in state['task']['nodes'] if n['kind']=='recover'),None)
                            row['recovery_probe']=dict(trigger=str(exc),node=node)
                            if node and node['status']=='ready':
                                recovered=run('recover','retreat_from_descent',dict(object='red cube'))
                                row['recovery_probe'].update(verification=recovered.get('skill_verification'),followup=e.observe()['system']['task'])
                        if args.alternate_recovery and placement_attempt<2:
                            node=next((n for n in e.observe()['system']['task']['nodes'] if n['kind']=='reorient'),None)
                            if node and node['status']=='ready':
                                run('reorient','reorient_held_object',dict(object='red cube'))
                                continue
                        raise
                if e.finish(e.revision)['status'] != 'verified':
                    run('verify', 'inspect_placement', dict(object='red cube', support='green cube'))
        except Exception as exc:
            row['error'] = str(exc)
        finally:
            time.sleep(protocol['settle_seconds'])
            row.update(public_final=e.finish(e.revision), system=e.observe().get('system'), used=e.state()['used'])
            e.close()
            row['closed'] = e.state()['status']
            rows.append(row)
            (args.records/'results.json').write_text(json.dumps(rows, indent=2))
            print(json.dumps(dict(task=task, public=row['public_final']['status'], error=row.get('error'))), flush=True)


if __name__ == '__main__':
    main()
