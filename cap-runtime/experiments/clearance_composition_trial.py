"""Controlled real-CaP clearance regression; not a natural-task benchmark.

The controller deliberately ends its post-release retreat 4 mm below the public
clearance requirement. With 3 mm endpoint tolerance this guarantees a gate gap.
Only the separate, closed-episode scorer may read simulator truth.
"""
import argparse
import json
import os
import time
from pathlib import Path

from agentic_cap.cap_backend import CaPBackend
from agentic_cap.core import Manager
from agentic_cap.placement_evidence import clearance_evidence
from experiments.active_view_trial import wait_for
from experiments.placement_inspection_trial import PlacementTrial


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--upstream', required=True)
    parser.add_argument('--records', type=Path, required=True)
    args = parser.parse_args()
    args.records.mkdir(parents=True, exist_ok=False)
    os.environ.update(CAP_ACTIVE_EVIDENCE='1', CAP_PLACEMENT_EVIDENCE='1', CAP_HOLDING_WATCHDOG='0')
    modes = ['green_after_release_until_lateral_view', 'green_after_release_persistent']
    protocol = dict(task='cube_stack', seed=62, modes=modes, shortfall_m=.004,
                    controller='fixed public-sensor Python; no LLM', settle_seconds=1.5,
                    scope='synthetic post-release occlusion and clearance gap; not natural success rate')
    (args.records/'protocol.json').write_text(json.dumps(protocol, indent=2))
    manager = Manager(args.records, lambda e: CaPBackend(e, args.upstream, evaluation=True))
    rows = []
    for index, mode in enumerate(modes):
        os.environ['CAP_SUPPORT_OCCLUSION'] = mode
        state = manager.create(f'clearance-{index}', 'cube_stack', 62,
                               dict(on=['red cube', 'green cube'], empty_gripper=True))
        episode = manager.episodes[state['id']]
        trial = PlacementTrial(episode, 'support_active')
        row = dict(mode=mode, episode_id=episode.id)
        try:
            wait_for(lambda: episode.snapshot or episode.status == 'failed', 150)
            trial.measure('initial')
            held = trial.pick('red cube')
            held = trial.prepare_placement(held, 'red cube', 'green cube')
            trial.place(held, 'red cube', 'green cube')
            snapshot = episode.observe()
            gate = clearance_evidence(episode.backend, snapshot, episode.backend.placement_release)
            z = gate['required_tcp_z_m'] - protocol['shortfall_m']
            if z <= episode.backend.placement_release['release_tcp_xyz'][2]:
                raise RuntimeError('Diagnostic target not above the recorded release')
            trial.segment('deliberately-short-retreat',
                          's=get_execution_state()\np=s["tcp_xyz"]\n'
                          f'p[2]={z!r}\ngoto_pose(p,s["tcp_wxyz"])')
            row['before_clearance'] = clearance_evidence(episode.backend, episode.observe(),
                                                        episode.backend.placement_release)
            if row['before_clearance'].get('reason') != 'vertical_retreat_not_confirmed':
                raise RuntimeError('Clearance gap was not covered')
            row['before_inspection'] = episode.finish(episode.revision)
            if row['before_inspection']['status'] != 'unknown':
                raise RuntimeError('Unknown relation was not covered')
            try:
                trial.segment('inspect-placement', 'inspect_placement("red cube", "green cube")')
            except RuntimeError as error:
                row['inspection_error'] = str(error)  # Expected for baseline/persistent unknown.
        except Exception as error:
            row['setup_error'] = str(error)
        finally:
            time.sleep(protocol['settle_seconds'])
            row.update(public_final=episode.finish(episode.revision), used=episode.state()['used'])
            row['acquisitions'] = [s.get('evidence_acquisition') for s in trial.segments
                                   if s.get('evidence_acquisition')]
            row['clearance_retreats'] = sum(e['kind'] == 'placement_clearance_retreat_started'
                                           for e in episode.events)
            episode.close()
            row['closed'] = episode.state()['status']
            rows.append(row)
            (args.records/'results.json').write_text(json.dumps(rows, indent=2))
            print(json.dumps({k: row.get(k) for k in ['mode', 'episode_id', 'setup_error',
                                                     'public_final', 'clearance_retreats']}), flush=True)


if __name__ == '__main__':
    main()
