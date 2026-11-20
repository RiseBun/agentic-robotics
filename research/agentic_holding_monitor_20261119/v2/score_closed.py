import hashlib
import json
import sys
import tarfile
from pathlib import Path
root = Path(__file__).resolve().parent
project = root.parents[2]
sys.path.insert(0, str(project/'cap-runtime'))
from experiments.score_active_view import stable_outcome
if not (root/'records').exists():
    with tarfile.open(root/'offline-json.tar.gz') as archive:
        for item in archive.getmembers():
            if root not in (root/item.name).resolve().parents or not item.name.startswith('records/') or not item.isfile():
                raise RuntimeError('Unexpected archive member')
        archive.extractall(root, filter='data')
records = root/'records'
rows = json.loads((records/'results.json').read_text())
if [r['mode'] for r in rows] != ['no_fault', 'actuator_release', 'red_pixels_missing']:
    raise RuntimeError('Incomplete diagnostic')
for row in rows:
    if json.loads((records/row['mode']/row['episode_id']/'episode.json').read_text())['status'] != 'closed':
        raise RuntimeError('Every episode must close before private scoring')
scored = []
for row in rows:
    directory = records/row['mode']/row['episode_id']
    events = [json.loads(s) for s in (directory/'events.jsonl').read_text().splitlines()]
    truth = [json.loads(s) for s in (directory/'evaluation-private.jsonl').read_text().splitlines()]
    close = next(e['time'] for e in reversed(events) if e['kind'] == 'episode_closed')
    outcome = stable_outcome(truth, close-1.25, close)
    trigger = next((e for e in events if e['kind'] == 'public_holding_contradicted'), None)
    injection = next((e for e in events if e['kind'] == 'diagnostic_actuator_fault_started'), None)
    confirmation = next((e for e in events if e['kind'] == 'public_holding_hold_confirmed'), None)
    false_samples = [s for s in truth if injection and s['time'] >= injection['time'] and s.get('task_completed') is False]
    final = row.get('public_final', {}).get('status', 'unknown')
    scored.append(dict(mode=row['mode'], episode_id=row['episode_id'], diagnosis_passed=row['passed'],
        physical_outcome=outcome, public_final=final,
        false_completion=final == 'verified' and outcome['status'] == 'failed',
        monitor_triggered=trigger is not None, hold_confirmed=confirmation is not None,
        injected_fault_to_detection_seconds=trigger['time']-injection['time'] if trigger and injection else None,
        first_private_failure_sample_to_detection_seconds=trigger['time']-false_samples[0]['time'] if trigger and false_samples else None,
        monitor_to_hold_seconds=confirmation['latency_seconds'] if confirmation else None,
        old_decision_rejected=row.get('old_decision_rejected'),
        late_probe_actions=sum(e['kind'] == 'diagnostic_late_action_executed' for e in events),
        reset_count=sum(e['kind'] == 'backend_configured' for e in events),
        source_unchanged=row['source_unchanged'], used=row['used']))
output = dict(job=20764, trials=scored, scorer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    scope='Three injected-fault/fixed-controller physical diagnoses, one seed; not natural slip, collision or robot-hardware evidence')
(root/'independent-score.json').write_text(json.dumps(output, indent=2))
print(json.dumps([{k:r[k] for k in ['mode', 'physical_outcome', 'public_final', 'monitor_triggered',
    'injected_fault_to_detection_seconds', 'monitor_to_hold_seconds', 'old_decision_rejected']} for r in scored]))
