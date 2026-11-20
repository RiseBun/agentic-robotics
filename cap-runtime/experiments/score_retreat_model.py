"""Offline model recovery diagnosis; all cases must close before truth reads."""
import argparse
import hashlib
import json
from pathlib import Path

from experiments.score_active_view import stable_outcome
from experiments.score_evidence_campaign import jsonl


def score(root):
    root = Path(root)
    protocol = json.loads((root/'campaign/protocol.json').read_text())
    admitted = []
    for case in protocol['cases']:
        folder = root/'campaign'/case['mode']
        result = json.loads((folder/'result.json').read_text())
        episode = root/(case['mode']+'-records')/case['episode_id']
        events = jsonl(episode/'events.jsonl')
        state = json.loads((episode/'episode.json').read_text())
        if (result['episode_id'] != case['episode_id'] or state['id'] != case['episode_id']
                or state['status'] != 'closed' or not events or events[-1]['kind'] != 'episode_closed'):
            raise ValueError('Every model diagnosis must have an authoritative close before private scoring')
        if not result.get('source_unchanged'):
            raise ValueError('Frozen model source changed')
        admitted.append((case, folder, result, episode, events, state))
    rows = []
    for case, folder, result, episode, events, state in admitted:
        truth = jsonl(episode/'evaluation-private.jsonl')
        model_events = [e for e in events if e['id'] > case['diagnostic']['handoff_event_id']]
        retreat = [e for e in model_events if e['kind'] == 'placement_retreat_checked' and e.get('allowed')]
        acquisitions = [e for e in model_events if e['kind'] == 'evidence_acquisition_finished']
        opens = [e for e in model_events if e['kind'] == 'api_result' and e.get('name') == 'open_gripper']
        release_starts = [e for e in model_events if e['kind'] == 'api_started' and e.get('name') == 'open_gripper'
                          and any(done.get('call_id') == e.get('call_id') for done in opens)]
        views = [e for e in model_events if e['kind'] == 'view_attempt']
        keys = [json.dumps(e.get('target_xyz')) for e in views]
        physical = stable_outcome(truth, events[-1]['time']-1.25, events[-1]['time'])
        decisions = []
        for file in (folder/'records').glob('*/workbench.jsonl'):
            decisions.extend(e['decision'] for e in jsonl(file) if e['kind'] == 'decision')
        # Contact proxy stops at intentional release, if one is executed.
        end = release_starts[0]['time'] if release_starts else events[-1]['time']
        contact = [s['bilateral_contact'].get('red cube') for s in truth
                   if retreat and retreat[0]['time'] <= s['time'] < end]
        held = 'unknown' if len(contact) < 2 else 'failed' if any(v is False for v in contact) else 'verified' if all(v is True for v in contact) else 'unknown'
        rows.append(dict(mode=case['mode'], episode_id=state['id'], outcome=result['outcome'],
                         independent_physical=physical, public_at_decision=result['public_at_decision']['status'],
                         false_completion=result['outcome'] == 'completed' and physical['status'] == 'failed',
                         model_decisions=len(decisions), decision_sequence=[d['decision'] for d in decisions],
                         autonomous_retreats=len(retreat),
                         support_inspections=sum(e['kind'] == 'api_started' and e.get('name') == 'inspect_support' for e in model_events),
                         support_available_after_retreat=bool(retreat) and any(
                             e['id'] > retreat[0]['id'] and
                             ((e['kind'] == 'placement_motion_checked' and e.get('evidence', {}).get('status') == 'available')
                              or (e['kind'] == 'evidence_acquisition_finished' and e.get('status') == 'available'))
                             for e in model_events),
                         acquisitions=[dict(status=e['status'], reason=e['reason'], views_used=e['views_used'], used_delta=e['used_delta']) for e in acquisitions],
                         views=len(views), duplicate_views=len(keys)-len(set(keys)),
                         successful_releases=len(opens),
                         release_gates=[dict(allowed=e['allowed'], evidence=e['evidence']) for e in model_events if e['kind'] == 'placement_release_checked'],
                         contact_before_intentional_release=dict(status=held, sample_count=len(contact), scope='bilateral contact proxy, not a calibrated slip detector'),
                         model_asked_user=any(d['decision'] == 'ask_user' for d in decisions),
                         reset_count=next((e['reset_count'] for e in events if e['kind'] == 'backend_configured'), None),
                         preparation_used=case['diagnostic']['preparation_used'], used=state['used'],
                         model_elapsed_seconds=result['terminal_at']-result['started_at']))
    return dict(model=protocol['model'], sources=protocol['sources']['digest'], rows=rows,
                scorer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                limitations=['One seen stack seed, two explicit sensor fault conditions, no algorithm comparator',
                             'Preparation fixed by a public controller; only post-failure recovery decisions are autonomous',
                             'All preparation and waiting costs remain in the episode budget; no statistical claim'])


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--records',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    result=score(args.records)
    args.output.write_text(json.dumps(result,indent=2))
    for r in result['rows']:
        print(json.dumps(dict(mode=r['mode'],retreats=r['autonomous_retreats'],physical=r['independent_physical']['status'],
                              public=r['public_at_decision'],outcome=r['outcome'],views=r['views'],released=r['successful_releases'])))


if __name__=='__main__':main()
