"""Offline recovery scoring. Admit all closed episodes before opening truth."""
import argparse
import hashlib
import json
from pathlib import Path

from experiments.score_evidence_campaign import jsonl
from experiments.score_active_view import stable_outcome


def score(root):
    root = Path(root)
    protocol = json.loads((root/'protocol.json').read_text())
    results = json.loads((root/'results.json').read_text())
    if [r['mode'] for r in results] != protocol['cases']:
        raise ValueError('All frozen retreat cases must finish before scoring')
    admitted = []
    for result in results:
        episode = root/result['episode_id']
        events = jsonl(episode/'events.jsonl')
        state = json.loads((episode/'episode.json').read_text())
        if not events or events[-1]['kind'] != 'episode_closed' or state['status'] != 'closed':
            raise ValueError('All retreat episodes require authoritative close')
        admitted.append((result, episode, events, state))
    rows = []
    for result, episode, events, state in admitted:
        truth = jsonl(episode/'evaluation-private.jsonl')
        gates = [e for e in events if e['kind'] == 'placement_release_checked']
        release_denied = next((e for e in gates if e.get('allowed') is False), None)
        after = [e for e in events if release_denied and e['id'] > release_denied['id']]
        contacts = [s['bilateral_contact'].get('red cube') for s in truth
                    if release_denied and s['time'] >= release_denied['time']]
        retreated = [e for e in events if e['kind'] == 'placement_retreat_checked' and e.get('allowed')]
        ends = [e for e in events if e['kind'] == 'evidence_acquisition_finished']
        starts = [e for e in events if e['kind'] == 'diagnostic_interrupt_checked']
        held_status = ('unknown' if len(contacts) < 2 else 'failed' if any(v is False for v in contacts)
                       else 'verified' if all(v is True for v in contacts) else 'unknown')
        rows.append(dict(mode=result['mode'], episode_id=result['episode_id'],
                         diagnosis_passed=result['diagnosis_passed'], error=result.get('error'),
                         release_denied=bool(release_denied), allowed_recorded_retreats=len(retreated),
                         gripper_calls_after_denial=sum(e['kind'] == 'api_started' and e.get('name')
                                                       in {'open_gripper', 'close_gripper'} for e in after),
                         successful_gripper_changes_after_denial=[e for e in after if e['kind'] == 'api_result'
                             and any(a.get('call_id') == e.get('call_id') and a.get('name') in {'open_gripper', 'close_gripper'}
                                     for a in after if a['kind'] == 'api_started')],
                         holding_contact_proxy=dict(status=held_status, sample_count=len(contacts),
                             scope='bilateral finger contact proxy after release denial; not calibrated slip detection'),
                         acquisitions=[dict(status=e['status'], reason=e['reason'], views_used=e['views_used'],
                                            used_delta=e['used_delta']) for e in ends],
                         interruption=starts[-1] if starts else None,
                         stop_latencies=[e['latency_seconds'] for e in after if e['kind'] == 'stop_confirmed'],
                         public_final=result['public_final']['status'],
                         recorded_goal_complete_at_end=stable_outcome(truth, events[-1]['time']-protocol['settle_seconds'], events[-1]['time']),
                         reset_count=next((e['reset_count'] for e in events if e['kind'] == 'backend_configured'), None),
                         used=state['used']))
    return dict(rows=rows, scope=protocol['scope'],
                scorer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                limitations=['Synthetic occlusion, fixed driver, seen stack seed 62',
                             'Recovery protocol diagnosis; tasks intentionally remain holding, not a completion benchmark',
                             'Private task score follows recorded goals, including explicit goal changes'])


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--records', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    result = score(args.records)
    args.output.write_text(json.dumps(result, indent=2))
    for r in result['rows']:
        print(json.dumps(dict(mode=r['mode'], passed=r['diagnosis_passed'],
                              held=r['holding_contact_proxy']['status'], retreats=r['allowed_recorded_retreats'],
                              public=r['public_final'], error=r['error'])))


if __name__ == '__main__':
    main()
