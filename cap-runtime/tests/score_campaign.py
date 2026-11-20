"""Offline private scoring. This module must never be imported by the policy.

Requires raw private scorer files after execution. Unknown/missing evidence is
kept unknown; it cannot silently turn a public completion into a true success.
"""
import argparse
import hashlib
import json
from pathlib import Path


def jsonl(file):
    return [json.loads(line) for line in file.read_text(encoding='utf-8').splitlines() if line] if file.exists() else []


def assess(row, samples, events, settle_seconds=1.2):
    # Use only the Runtime clock for matching physics and completion. The
    # workbench/server wall clocks need not be synchronized across machines.
    accepted = next((e for e in events if e['kind'] == 'finish_checked' and e.get('result', {}).get('status') == 'verified'), None)
    closed = next((e['time'] for e in reversed(events) if e['kind'] == 'episode_closed'), None)
    declared = row.get('outcome') == 'completed'
    terminal = accepted['time'] if accepted and declared else closed-settle_seconds if closed is not None else None
    post = [s for s in samples if terminal is not None and closed is not None and terminal <= s['time'] <= closed]
    # Two samples spanning half a second establish only the declared settle
    # window. They do not prove future stability or indefinite task competence.
    complete = len(post) >= 2 and post[-1]['time']-post[0]['time'] >= .5
    stable = (False if any(s.get('task_completed') is False for s in post)
              else True if all(s.get('task_completed') is True for s in post) else None) if complete else None
    contradicted = any(s.get('task_completed') is False for s in post) if post else None
    failures = []
    results_by_call = {}
    cancelled_segments = {e['segment']['id'] for e in events if e['kind'] == 'segment_finished' and e['segment']['execution'] == 'cancelled'}
    for event in events:
        kind = event['kind']
        result = event.get('result', {})
        key = event.get('call_id') or (event.get('segment_id'), event.get('name'))
        if kind == 'api_started':
            results_by_call.pop(key, None)
        elif kind == 'api_result':
            results_by_call[key] = result
        # Normalize older untyped records too. An unknown API result followed
        # by a generic exception is still insufficient evidence, not a failure.
        previous = results_by_call.get(key)
        unknown = isinstance(previous, dict) and previous.get('status') == 'unknown'
        if ((kind == 'api_error' and not unknown and event.get('segment_id') not in cancelled_segments and event.get('feedback', {}).get('category') not in {'insufficient_evidence', 'cancelled', 'budget_exhausted'})
                or (kind in {'precondition_checked', 'postcondition_checked'} and result.get('status') == 'failed')):
            failures.append(event)
    failure_before_completion = bool(terminal and any(e['time'] < terminal for e in failures))
    stop_latencies = [e['latency_seconds'] for e in events if e['kind'] == 'stop_confirmed']
    ready = next((e for e in events if e['kind'] == 'backend_configured'), {})
    return dict(independent_success=stable,
                verified_system_success=stable if declared else False,
                physical_success_unacknowledged=bool(stable and not declared),
                public_declared_completion=declared,
                false_completion=bool(declared and contradicted) if contradicted is not None else None,
                private_settle_samples=len(post), private_settle_span_seconds=post[-1]['time']-post[0]['time'] if post else 0,
                private_last=samples[-1].get('task_completed') if samples else None,
                failure_observed=bool(failures), recovered_after_observed_failure=bool(failure_before_completion and stable),
                reset_count=ready.get('reset_count'),
                max_stop_latency_seconds=max(stop_latencies) if stop_latencies else None)


def score(campaign, private_root):
    campaign, private_root = Path(campaign), Path(private_root)
    manifest = json.loads((campaign/'campaign.json').read_text(encoding='utf-8'))
    rows = []
    for trial in manifest['plan']:
        file = campaign/trial['trial_id']/'result.json'
        if not file.exists():
            rows.append({**trial, 'outcome': 'unattempted'})
            continue
        row = json.loads(file.read_text(encoding='utf-8'))
        directory = private_root/row.get('episode_id', 'missing')
        events = jsonl(directory/'events.jsonl')
        samples = jsonl(directory/'evaluation-private.jsonl')
        scored = assess(row, samples, events, settle_seconds=manifest['settle_milliseconds']/1000)
        records = list((campaign/trial['trial_id']/'records').glob('*/workbench.jsonl'))
        workbench = [e for file in records for e in jsonl(file)]
        scored['additional_user_inputs'] = max(0, sum(e['kind'] == 'user_input' for e in workbench)-1)
        rows.append({**row, **scored})
    attempted = [r for r in rows if r['outcome'] != 'unattempted']
    return dict(version=2, scorer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), source_digest=manifest['sources']['digest'], model=manifest['model']['model'],
                planned=len(rows), attempted=len(attempted),
                independent_successes=sum(r.get('independent_success') is True for r in attempted),
                verified_system_successes=sum(r.get('verified_system_success') is True for r in attempted),
                unknown_independent_outcomes=sum(r.get('independent_success') is None for r in attempted),
                false_completions=sum(r.get('false_completion') is True for r in attempted),
                infrastructure_errors=sum(r['outcome'] in {'infrastructure_error', 'interrupted'} for r in attempted),
                scope='Diagnostic pilot; all attempts retained. Private physical completion and verified system completion reported separately. No statistical generalization claim.', rows=rows)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('campaign')
    parser.add_argument('private_root')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = score(args.campaign, args.private_root)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'rows'}, ensure_ascii=False))
