"""Closed-only interruption audit; evaluator contact is never an online input."""
import hashlib
import json
import tarfile
from pathlib import Path

root = Path(__file__).resolve().parent
if not (root / 'records').exists():
    with tarfile.open(root / 'offline-json.tar.gz') as archive:
        for member in archive.getmembers():
            target = (root / member.name).resolve()
            if (root not in target.parents or not member.isfile()
                    or member.name.split('/')[0] != 'records'):
                raise RuntimeError('Unexpected archive member')
        archive.extractall(root, filter='data')

records = root / 'records'
rows = json.loads((records / 'results.json').read_text())
if len(rows) != 2 or {r['mode'] for r in rows} != {'stop', 'goal_change'}:
    raise RuntimeError('Require both frozen cases')
admitted = []
for row in rows:
    directory = records / row['episode_id']
    state = json.loads((directory / 'episode.json').read_text())
    events = [json.loads(s) for s in (directory / 'events.jsonl').read_text().splitlines()]
    if state['status'] != 'closed' or events[-1]['kind'] != 'episode_closed':
        raise RuntimeError('All episodes must close before private scoring')
    admitted.append((row, directory, state, events))

output = []
for row, directory, state, events in admitted:
    sid = row['inspection_segment']['id']
    starts = [e for e in events if e['kind'] == 'view_attempt' and e.get('segment_id') == sid]
    after = [e for e in events if e['id'] > starts[0]['id']]
    confirmations = [e for e in after if e['kind'] == 'stop_confirmed']
    cutoff = confirmations[0]['time'] if confirmations else starts[0]['time']
    truth = [json.loads(s) for s in (directory / 'evaluation-private.jsonl').read_text().splitlines()]
    samples = [s for s in truth if s['time'] >= cutoff]
    contacts = [s.get('bilateral_contact', {}).get('red cube') for s in samples]
    span = samples[-1]['time'] - samples[0]['time'] if samples else 0
    contact_status = ('unknown' if len(samples) < 2 or span < 1
                      else 'verified' if all(c is True for c in contacts)
                      else 'failed' if any(c is False for c in contacts) else 'unknown')
    output.append(dict(mode=row['mode'], episode_id=row['episode_id'],
        diagnosis_passed=row['passed'], actual_motion_covered=row['coverage_reached'],
        source_unchanged=row['source_unchanged'], views_used=row['view_count'],
        execution=row['inspection_segment']['execution'],
        late_old_api_starts=len(row['late_old_api_starts']),
        late_release=row['release_after_cancel'], old_revision_rejected=row.get('old_revision_rejected'),
        stop_latencies_seconds=[e['latency_seconds'] for e in confirmations],
        public_goal_status=row['public_final']['status'],
        held_contact_proxy=dict(status=contact_status, samples=len(samples), span_seconds=span,
            scope='bilateral finger contact after first stop confirmation; not task completion or calibrated slip detection'),
        reset_count=next((e['reset_count'] for e in events if e['kind']=='backend_configured'), None),
        used=state['used']))

result = dict(cases=output, scorer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    scope='Fixed public driver, synthetic support occlusion, stack seed 76; interruption diagnosis, no LLM or success-rate comparison')
(root / 'closed-audit.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False))
