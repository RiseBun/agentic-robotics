"""Engineering-only handoff: prepare public failure, then serve normal APIs.

No extra product route, model key, truth feedback, or reset during recovery.
The diagnostic's preparation cost stays in the original episode ledger.
"""
import argparse
import json
import os
from pathlib import Path

from agentic_cap.core import Manager
from agentic_cap.server import runtime_provenance, serve
from experiments.active_view_trial import wait_for
from experiments.vertical_retreat_trial import DescentFaultBackend, RetreatTrial, source_hashes


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--upstream', required=True)
    p.add_argument('--records', type=Path, required=True)
    p.add_argument('--port', type=int, required=True)
    p.add_argument('--mode', choices=['retreat_recovers', 'persistent_unknown'], required=True)
    p.add_argument('--task', choices=['cube_stack', 'cube_restack'], default='cube_stack')
    p.add_argument('--seed', type=int, default=62)
    p.add_argument('--label', default='default')
    args = p.parse_args()
    args.records = args.records.resolve()
    if args.records.exists():
        raise ValueError('Use a new diagnostic directory; never replay preparation')
    token = os.environ['CAP_RUNTIME_TOKEN']
    os.environ.update(CAP_ACTIVE_EVIDENCE='1', CAP_PLACEMENT_EVIDENCE='1', CAP_SUPPORT_OCCLUSION='')
    hashes = source_hashes()
    manager = Manager(args.records, lambda e: DescentFaultBackend(e, args.upstream, args.mode))
    state = manager.create(f'prepared-retreat-{args.mode}-{args.task}-{args.seed}-{args.label}', args.task, args.seed,
                           dict(on=['red cube', 'green cube'], empty_gripper=True))
    e = manager.episodes[state['id']]
    trial = RetreatTrial(e, 'support_active')
    diagnostic = dict(mode=args.mode, task=args.task, seed=args.seed, label=args.label, episode_id=e.id, prepared=False, source_sha256=hashes,
                      scope='fixed public preparation followed by model-only recovery; not end-to-end task benchmark')
    try:
        wait_for(lambda: e.snapshot or e.status == 'failed', 150)
        if e.status == 'failed':
            raise RuntimeError(str(next(x for x in reversed(e.events) if x['kind'] == 'runtime_failed')))
        trial.measure('initial')
        if args.task == 'cube_restack':
            measurement = trial.pick('green cube')
            trial.place(measurement, 'green cube', 'red cube', aside=True)
        trial.reach_low_pose()
        if source_hashes() != hashes:
            raise RuntimeError('Frozen preparation source changed')
        event = e.event('diagnostic_model_handoff_ready', mode=args.mode,
                        used=e.state()['used'], fixed_segment_ids=[s['id'] for s in trial.segments])
        diagnostic.update(prepared=True, handoff_event_id=event['id'], preparation_used=e.state()['used'])
    except Exception as exc:
        diagnostic['error'] = str(exc)
        e.close()
    provenance = runtime_provenance('cap', args.upstream)
    provenance['diagnostic'] = diagnostic
    (args.records/'preparation.json').write_text(json.dumps(diagnostic, indent=2))
    server = serve(manager, '127.0.0.1', args.port, token, provenance)
    print(json.dumps(diagnostic), flush=True)
    try:
        server.serve_forever()
    finally:
        e.close()
        server.server_close()


if __name__ == '__main__':
    main()
