"""All closed before independent scoring. No policy import of evaluator data."""
import hashlib
import json
import sys
import tarfile
from pathlib import Path

root = Path(__file__).resolve().parent
project = root.parents[2]
sys.path.insert(0, str(project/'cap-runtime/tests'))
from score_campaign import assess, jsonl

host = root/'campaign'
if not (host/'host-result.json').is_file(): raise RuntimeError('Model host not complete')
protocol = json.loads((host/'paired-campaign.json').read_text())
if [c['variant'] for c in protocol['schedule']] != ['recoverable', 'occluded']:
    raise RuntimeError('Unexpected frozen protocol')
if not (root/'recoverable-records').exists():
    with tarfile.open(root/'offline-json.tar.gz') as archive:
        for member in archive.getmembers():
            if (root not in (root/member.name).resolve().parents or not member.isfile()
                    or member.name.split('/')[0] not in {'recoverable-records','occluded-records'}):
                raise RuntimeError('Unexpected member')
        archive.extractall(root, filter='data')
admitted = []
for case in protocol['schedule']:
    folder = host/case['pair_id']/case['variant']
    plan = json.loads((folder/'campaign.json').read_text())['plan']
    if len(plan) != 1: raise RuntimeError('Unexpected trial count')
    trial = folder/plan[0]['trial_id']
    row = json.loads((trial/'result.json').read_text())
    episode = root/(case['variant']+'-records')/row.get('episode_id', 'missing')
    state = json.loads((episode/'episode.json').read_text()) if row.get('episode_id') else None
    if state is not None and state['status'] != 'closed': raise RuntimeError('Close both episodes first')
    admitted.append((case, trial, row, episode, state))

scored = []
for case, trial, row, episode, state in admitted:
    events = jsonl(episode/'events.jsonl')
    physical = assess(row, jsonl(episode/'evaluation-private.jsonl'), events, settle_seconds=1.25)
    starts = [e for e in events if e['kind'] == 'diagnostic_actuator_fault_started']
    finished = [e for e in events if e['kind'] == 'diagnostic_actuator_fault_finished']
    alerts = [e for e in events if e['kind'] == 'public_holding_contradicted']
    confirmation = [e for e in events if e['kind'] == 'public_holding_hold_confirmed']
    rearmed = [e for e in events if e['kind'] == 'public_holding_monitor_rearmed']
    mask = [e for e in events if e['kind'] == 'diagnostic_red_sensor_fault_started']
    trigger = alerts[0] if alerts else None
    segments = state['segments'] if state else {}
    late_apis = [e for e in events if trigger and e['id'] > trigger['id'] and e['kind'] == 'api_started'
        and segments.get(e.get('segment_id'), {}).get('revision', trigger['revision']) < trigger['revision']]
    fresh_holds = [e for e in events if trigger and e['id'] > trigger['id'] and e['kind'] == 'api_result'
                   and e['name'] == 'check_holding' and isinstance(e['result'], dict) and e['result'].get('status') == 'verified']
    workbench = [e for file in (trial/'records').glob('*/workbench.jsonl') for e in jsonl(file)]
    abandoned_models = [e for e in workbench if e['kind'] == 'stale_decision_discarded'
                        and 'Public holding monitor invalidated' in e.get('reason', '')]
    created_goal = next((e['goal'] for e in events if e['kind'] == 'episode_created'), None)
    limits = state['limits'] if state else {}
    used = state['used'] if state else {}
    row.update(case, **physical, actuator_fault_count=len(starts), actuator_fault_finished_count=len(finished),
        monitor_count=len(alerts), monitor_during_active_segment=bool(trigger and trigger.get('segment_id')),
        coverage_reached=bool(len(starts)==1 and len(finished)==1 and len(alerts)==1 and confirmation),
        abandoned_model_requests=len(abandoned_models), late_old_revision_api_starts=len(late_apis),
        verified_regrasp_count=len(fresh_holds), monitor_rearmed_count=len(rearmed), persistent_mask_started=bool(mask),
        original_goal_preserved=bool(state and state['goal']==created_goal),
        budget_ledger_matches=bool(state and used['decisions']==sum(e['kind']=='decision_admitted' for e in events)
            and used['calls']==sum(e['kind']=='api_started' for e in events)),
        budget_not_reset=all(used.get(k, 0) <= v+.25 if k=='wall_seconds' else used.get(k,0) <= v for k,v in limits.items()),
        fault_to_monitor_seconds=trigger['time']-starts[0]['time'] if trigger and starts else None,
        monitor_to_hold_seconds=confirmation[0]['latency_seconds'] if confirmation else None,
        additional_user_inputs=max(0,sum(e['kind']=='user_input' for e in workbench)-1),
        used_authoritative=used,
        mechanism='model recovered original goal' if trigger and fresh_holds and rearmed and physical['verified_system_success'] else 'no verified model recovery')
    scored.append(row)
output = dict(job=20766, cases=scored, model=protocol['model'],
    scorer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    base_scorer_sha256=hashlib.sha256((project/'cap-runtime/tests/score_campaign.py').read_bytes()).hexdigest(),
    scope='One injected actuator fault per condition; actual model from initial scene; no algorithm comparator, natural-slip or general success-rate claim')
(root/'independent-score.json').write_text(json.dumps(output,ensure_ascii=False,indent=2))
print(json.dumps([{k:r[k] for k in ['variant','outcome','independent_success','public_declared_completion',
    'coverage_reached','monitor_during_active_segment','abandoned_model_requests','verified_regrasp_count',
    'monitor_rearmed_count','late_old_revision_api_starts','false_completion','mechanism']} for r in scored]))
