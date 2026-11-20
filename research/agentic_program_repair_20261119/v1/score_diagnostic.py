"""Score the original closed diagnostic without retrying its interrupted task."""
import hashlib
import json
import sys
import tarfile
from pathlib import Path

root=Path(__file__).resolve().parent
project=root.parents[2]
sys.path.insert(0,str(project/'cap-runtime/tests'))
from score_campaign import assess,jsonl

if not (root/'repair-records').exists():
    with tarfile.open(root/'closed-diagnostic-json.tar.gz') as archive:
        for member in archive.getmembers():
            if (root not in (root/member.name).resolve().parents or not member.isfile()
                    or member.name.split('/')[0]!='repair-records'):
                raise RuntimeError('Unexpected archive member')
        archive.extractall(root,filter='data')
audit=json.loads((root/'interruption-audit.json').read_text())
if audit['normal'] or len(audit['repair'])!=1:
    raise RuntimeError('Require verified original admission')
identity=audit['repair'][0]['episode_id']
directory=root/'repair-records'/identity
state=json.loads((directory/'episode.json').read_text())
events=jsonl(directory/'events.jsonl')
if state['status']!='closed' or events[-1]['kind']!='episode_closed':
    raise RuntimeError('Original episode must close before evaluator scoring')
result=json.loads((root/'repair-campaign/field_access/result.json').read_text())
workbench=next((root/'repair-campaign/field_access/records').glob('*/workbench.jsonl'))
rows=jsonl(workbench)
decisions=[r['decision'] for r in rows if r['kind']=='decision']
handoff=next(e for e in events if e['kind']=='diagnostic_model_handoff_ready')
after=[e for e in events if e['id']>handoff['id']]
held=[e for e in after if e['kind']=='api_result' and e.get('name')=='check_holding'
      and e.get('result',{}).get('status')=='verified']
first_segment=next((e['segment'] for e in after if e['kind']=='segment_finished'),{})
truth=jsonl(directory/'evaluation-private.jsonl')
scores=assess(result,truth,events,1.5)
green_hold=next((e for e in held if e.get('result',{}).get('evidence',{}).get('object_name')=='green cube'),None)
release=next((e for e in after if green_hold and e['id']>green_hold['id']
              and e['kind']=='api_started' and e.get('name')=='open_gripper'),None)
contact_samples=[s for s in truth if green_hold and release and green_hold['time']<=s['time']<release['time']]
contacts=[s.get('bilateral_contact',{}).get('green cube') for s in contact_samples]
span=contact_samples[-1]['time']-contact_samples[0]['time'] if contact_samples else 0
contact_status=('unknown' if len(contacts)<2 or span<.5 else 'failed' if any(c is False for c in contacts)
                else 'verified' if all(c is True for c in contacts) else 'unknown')
invalid=next(s for s in state['segments'].values() if s['subgoal']=='invalid-state-field')
output=dict(episode_id=identity,original_outcome=result['outcome'],
    trial_failure='transport_error',close_request_error=result.get('close_error'),
    authority_close=state['termination_reason'],used=state['used'],
    diagnostic_error_feedback=invalid['feedback'],
    first_model_decision=decisions[0] if decisions else None,
    first_model_segment_execution=first_segment.get('execution'),
    first_model_segment_postcondition=first_segment.get('stage_verification'),
    verified_green_holding_after_repair=bool(green_hold),
    repaired_stage_contact_proxy=dict(status=contact_status,samples=len(contacts),span_seconds=span,
        scope='bilateral green finger contact from public repaired-stage holding check until next release request; not task completion'),
    source_unchanged=result['source_unchanged'],
    reset_count=next(e['reset_count'] for e in events if e['kind']=='backend_configured'),
    independent=scores,
    budget_ledger_matches=state['used']['calls']==sum(e['kind']=='api_started' for e in events)
      and state['used']['decisions']==sum(e['kind']=='decision_admitted' for e in events),
    scorer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    scope='Original interrupted prepared case, one model repair diagnostic; not full-task success or causal comparison')
(root/'diagnostic-score.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:output[k] for k in ['original_outcome','authority_close','verified_green_holding_after_repair',
    'first_model_segment_execution','source_unchanged','reset_count','budget_ledger_matches','independent']}))
