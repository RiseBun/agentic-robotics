"""Offline only: admit every closed case before reading any private outcome."""
import argparse
import hashlib
import json
from pathlib import Path

from experiments.score_active_view import stable_outcome
from experiments.score_evidence_campaign import jsonl


def score(root):
    root = Path(root)
    protocol = json.loads((root/'protocol.json').read_text())
    rows = json.loads((root/'results.json').read_text())
    if len(rows) != len(protocol['cases']) or [r['mode'] for r in rows] != protocol['cases']:
        raise ValueError('Every frozen diagnosis must finish before private scoring')
    admitted = []
    for row in rows:
        events = jsonl(root/row['episode_id']/'events.jsonl')
        if not events or events[-1]['kind'] != 'episode_closed':
            raise ValueError('Authoritative episode close missing')
        admitted.append((row, events))
    scored = []
    for row, events in admitted:
        closed = events[-1]['time']
        physical = stable_outcome(jsonl(root/row['episode_id']/'evaluation-private.jsonl'),
                                  closed-protocol['settle_seconds'], closed)
        starts = [event for event in events if event['kind'] == 'evidence_acquisition_started'
                  and event.get('acquisition_type') == 'placement_verification']
        acquisitions = [event for event in events if event['kind'] == 'evidence_acquisition_finished'
                        and event.get('acquisition_type') == 'placement_verification']
        after = [event for event in events if starts and event['id'] > starts[0]['id']]
        scored.append(dict(mode=row['mode'], episode_id=row['episode_id'],
                           independent_physical=physical, public_final=row['public_final']['status'],
                           skill_reached=bool(starts), acquisition=acquisitions[-1] if acquisitions else None,
                           gripper_calls_after_inspection=sum(event['kind'] == 'api_started' and event.get('name')
                                                            in {'open_gripper', 'close_gripper'} for event in after),
                           reset_count=next((event['reset_count'] for event in events if event['kind'] == 'backend_configured'), None),
                           false_public_confirmation=row['public_final']['status'] == 'verified' and physical['status'] == 'failed',
                           used=row['used']))
    return dict(rows=scored, scope=protocol['scope'],
                scorer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--records', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    result = score(args.records)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    for row in result['rows']:
        print(json.dumps(dict(mode=row['mode'], physical=row['independent_physical']['status'],
                              public=row['public_final'], reached=row['skill_reached'],
                              views=(row['acquisition'] or {}).get('views_used'))))


if __name__ == '__main__':
    main()
