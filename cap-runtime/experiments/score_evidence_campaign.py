"""Offline evidence experiment scoring; never import into the robot policy.

Every scheduled result and authoritative episode_closed event are required
before any private outcome file is read. Missing/weak truth remains unknown.
"""
import argparse
import hashlib
import json
from pathlib import Path

from experiments.score_active_view import stable_outcome


def jsonl(file):
    return [json.loads(line) for line in file.read_text(encoding='utf-8-sig').splitlines() if line]


def evidence_metrics(events):
    acquisitions = [event for event in events if event['kind'] == 'evidence_acquisition_finished'
                    and event.get('acquisition_type') != 'placement_verification']
    placement = [event for event in events if event['kind'] == 'evidence_acquisition_finished'
                 and event.get('acquisition_type') == 'placement_verification']
    views = [event for event in events if event['kind'] == 'view_attempt']
    identities = [(event.get('acquisition_id'), tuple(event.get('target_xyz', [])),
                   tuple(event.get('target_wxyz', []))) for event in views]
    # Repeated absolute poses across distinct grasps can be legitimate; only
    # duplicates within one acquisition are an unambiguous repeated view.
    duplicates = len(identities) - len(set(identities))
    unknowns = [event for event in acquisitions if event.get('before', {}).get('status') == 'unknown']
    recovered = sum(event.get('status') == 'available' for event in unknowns)
    blocked = [event for event in events if event['kind'] == 'api_error'
               and event.get('name') in {'goto_pose', 'open_gripper'}
               and 'Support evidence unknown' in str(event.get('feedback', {}).get('evidence', {}).get('reason', ''))]
    successful_releases = [event for event in events if event['kind'] == 'api_result' and event.get('name') == 'open_gripper']
    release_gates = [event for event in events if event['kind'] == 'placement_release_checked']
    return dict(inspect_support_calls=sum(event['kind'] == 'api_started' and event.get('name') == 'inspect_support' for event in events),
                inspect_placement_calls=sum(event['kind'] == 'api_started' and event.get('name') == 'inspect_placement' for event in events),
                placement_acquisitions=len(placement),
                placement_unknown_to_verified=sum(event.get('before', {}).get('status') == 'unknown'
                                                   and event.get('status') == 'verified' for event in placement),
                placement_acquisition_costs=[dict(status=event.get('status'), views=event.get('views_used'),
                                                 used=event.get('used_delta'), wall_seconds=event.get('elapsed_seconds'))
                                             for event in placement],
                acquisition_results=len(acquisitions), unknown_acquisitions=len(unknowns),
                unknown_to_available=recovered,
                unknown_to_available_ratio=recovered/len(unknowns) if unknowns else None,
                view_attempts=len(views), duplicate_views_within_acquisition=duplicates,
                blocked_placement_attempts=len(blocked),
                acquisition_costs=[dict(status=event.get('status'), used=event.get('used_delta'),
                                       wall_seconds=event.get('elapsed_seconds')) for event in acquisitions],
                # Successful-release evidence was not recorded at every API
                # boundary in this protocol. Do not turn that gap into zero.
                unknown_evidence_release_violations=None,
                recorded_release_gates=len(release_gates), successful_open_calls=len(successful_releases),
                release_violation_audit='Requires per-release public gate evidence; not inferred from missing errors')


def score(campaign, private_roots):
    campaign = Path(campaign)
    manifest = json.loads((campaign/'paired-campaign.json').read_text(encoding='utf-8-sig'))
    admitted = []
    # Admission audit first: no peeking at truth while any trial is live.
    for scheduled in manifest['schedule']:
        trial_id = f"{scheduled['task']}-s{scheduled['seed']}-r0"
        folder = campaign/scheduled['pair_id']/scheduled['variant']/trial_id
        file = folder/'result.json'
        if not file.is_file():
            raise ValueError('All scheduled trials must finish before private scoring: ' + str(folder))
        result = json.loads(file.read_text(encoding='utf-8-sig'))
        episode_id = result.get('episode_id')
        if not episode_id:
            admitted.append((scheduled, result, [], None, folder))
            continue
        root = Path(private_roots[scheduled['variant']])/episode_id
        events = jsonl(root/'events.jsonl')
        if not any(event['kind'] == 'episode_closed' for event in events):
            raise ValueError('Episode has no authoritative close: ' + episode_id)
        admitted.append((scheduled, result, events, root, folder))
    rows = []
    for scheduled, result, events, root, folder in admitted:
        if root is None:
            rows.append(dict(**scheduled, outcome=result['outcome'], independent_physical='unknown',
                             error=result.get('error'), episode_id=None))
            continue
        closed = next(event['time'] for event in reversed(events) if event['kind'] == 'episode_closed')
        samples = jsonl(root/'evaluation-private.jsonl')
        physical = stable_outcome(samples, closed-manifest['settle_milliseconds']/1000, closed)
        runtime_record = json.loads((root/'episode.json').read_text(encoding='utf-8-sig'))
        if runtime_record.get('id') != result['episode_id'] or runtime_record.get('status') != 'closed':
            raise ValueError('Authoritative final Runtime cost record missing')
        public = result.get('public_at_decision', {}).get('status', 'unknown')
        declared = result['outcome'] == 'completed'
        workbench = [event for file in (folder/'records').glob('*/workbench.jsonl') for event in jsonl(file)]
        rows.append(dict(**scheduled, episode_id=result['episode_id'], outcome=result['outcome'],
                         error=result.get('error'), close_error=result.get('close_error'),
                         independent_physical=physical['status'], independent_evidence=physical,
                         autonomous_public=public,
                         false_completion=None if declared and physical['status'] == 'unknown' else declared and physical['status'] == 'failed',
                         verified_system_success=declared and physical['status'] == 'verified',
                         additional_user_inputs=max(0, sum(event['kind'] == 'user_input' for event in workbench)-1),
                         used=runtime_record.get('used'), cost_source='Runtime episode.json after confirmed close',
                         client_recorded_used_at_close=result.get('used_at_close'), total_seconds=result.get('total_seconds'),
                         **evidence_metrics(events)))
    groups = {}
    for variant in sorted({row['variant'] for row in rows}):
        group = [row for row in rows if row['variant'] == variant]
        groups[variant] = dict(trials=len(group), independent_physical_successes=sum(row['independent_physical'] == 'verified' for row in group),
                               infrastructure_errors=sum(row['outcome'] == 'infrastructure_error' for row in group),
                               verified_system_successes=sum(row.get('verified_system_success', False) for row in group),
                               false_completions=sum(row.get('false_completion') is True for row in group),
                               unknown_independent=sum(row['independent_physical'] == 'unknown' for row in group),
                               inspect_support_calls=sum(row.get('inspect_support_calls', 0) for row in group),
                               inspect_placement_calls=sum(row.get('inspect_placement_calls', 0) for row in group),
                               placement_unknown_to_verified=sum(row.get('placement_unknown_to_verified', 0) for row in group),
                               unknown_to_available=sum(row.get('unknown_to_available', 0) for row in group))
    return dict(model=manifest['model'], groups=groups, rows=rows,
                scorer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                limitations=['One trial per task/seed/condition; diagnostic only',
                             'Physical success concerns the recorded end window only',
                             'Missing per-release evidence is unscored, never assumed safe'])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--baseline-private', type=Path, required=True)
    parser.add_argument('--candidate-private', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = score(args.campaign, dict(baseline=args.baseline_private, candidate=args.candidate_private))
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(result['groups'], indent=2))


if __name__ == '__main__':
    main()
