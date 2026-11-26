"""Separate offline scorer. Require all frozen episodes closed before truth I/O."""
import argparse
import json
from pathlib import Path


def score(root):
    protocol = json.loads((root/'protocol.json').read_text())
    rows = json.loads((root/'results.json').read_text())
    if [row['task'] for row in rows] != protocol['tasks'] or len({r['episode_id'] for r in rows}) != len(rows):
        raise RuntimeError('Incomplete or duplicate case set; refusing private scoring')
    closed = []
    for row in rows:
        folder = root/row['episode_id']
        state = json.loads((folder/'episode.json').read_text())
        events = [json.loads(line) for line in (folder/'events.jsonl').read_text().splitlines() if line]
        if state['status'] != 'closed' or not events or events[-1]['kind'] != 'episode_closed':
            raise RuntimeError('An episode is still open; refusing private scoring')
        closed.append((row, folder, state, events))
    results = []
    for row, folder, state, events in closed:
        samples = [json.loads(line) for line in (folder/'evaluation-private.jsonl').read_text().splitlines() if line]
        end = events[-1]['time']
        settled = [sample for sample in samples if end-protocol['settle_seconds'] <= sample['time'] <= end]
        span = settled[-1]['time']-settled[0]['time'] if settled else 0
        success = all(sample['task_completed'] is True for sample in settled) if len(settled) >= 2 and span >= .5 else None
        public = row['public_final']['status']
        phases = []
        for segment in state['segments'].values():
            phases.append(dict(subgoal=segment['subgoal'], skill=(segment.get('skill') or {}).get('name'),
                execution=segment['execution'], stage=segment.get('stage_verification', {}).get('status'),
                skill_effect=segment.get('skill_verification', {}).get('status'), error=segment.get('error'),
                feedback=segment.get('feedback')))
        result = dict(task=row['task'], episode_id=row['episode_id'], error=row.get('error'),
            public_at_close=public, independent_settle_success=success, settle_samples=len(settled),
            settle_span_seconds=span, false_confirmation=public == 'verified' and success is False,
            reset_count=next((e.get('reset_count') for e in events if e['kind'] == 'backend_configured'), None),
            budget_matches_events=state['used']['calls'] == sum(e['kind'] == 'api_started' for e in events),
            public_goal_matches_system=row['system']['world']['facts']['goal']['status'] == public,
            continuous_control=row.get('continuous_control'), used=state['used'], phases=phases)
        results.append(result)
    return dict(scope=protocol['scope'], seed=protocol['seed'], model=None, cases=results)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('records', type=Path)
    args = parser.parse_args()
    result = score(args.records)
    (args.records/'independent-summary.json').write_text(json.dumps(result, indent=2))
    print(json.dumps({**result, 'cases': [{k: v for k, v in case.items() if k != 'phases'} for case in result['cases']]}))
