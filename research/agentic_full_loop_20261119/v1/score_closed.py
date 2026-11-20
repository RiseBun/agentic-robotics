import hashlib
import json
import sys
import tarfile
from pathlib import Path

root=Path(__file__).resolve().parent
project=root.parents[2]
sys.path.insert(0,str(project/'cap-runtime/tests'))
from score_campaign import assess, jsonl

admitted=json.loads((root/'closed-admission.json').read_text())
if len(admitted)!=4:raise RuntimeError('Require all four admissions')
if not (root/'normal-records').exists():
    with tarfile.open(root/'offline-json.tar.gz') as archive:
        for member in archive.getmembers():
            if (root not in (root/member.name).resolve().parents or not member.isfile()
                    or member.name.split('/')[0] not in {'normal-records','active-records'}):raise RuntimeError('Unexpected archive path')
        archive.extractall(root,filter='data')
# Check every environment's closure before reading any private evaluator sample.
for admission in admitted:
    if not admission.get('episode_id'):continue
    directory=root/(admission['variant']+'-records')/admission['episode_id']
    if json.loads((directory/'episode.json').read_text())['status']!='closed':raise RuntimeError('Close every case first')
rows=[]
for admission in admitted:
    variant=admission['variant']
    campaign=root/(variant+'-campaign')
    options=json.loads((root/(variant+'-options.json')).read_text())
    manifest=json.loads((campaign/'campaign.json').read_text())
    if manifest['model']!=options['expectedModel']:raise RuntimeError('Configured model differs from intended protocol')
    trial=campaign/admission['trial_id']
    row=json.loads((trial/'result.json').read_text())
    directory=root/(variant+'-records')/row.get('episode_id','missing')
    events=jsonl(directory/'events.jsonl')
    state=json.loads((directory/'episode.json').read_text())
    row.update(variant=variant,**assess(row,jsonl(directory/'evaluation-private.jsonl'),events,1.5))
    starts=[e for e in events if e['kind']=='diagnostic_actuator_fault_started']
    alerts=[e for e in events if e['kind']=='public_holding_contradicted']
    confirmations=[e for e in events if e['kind']=='public_holding_hold_confirmed']
    trigger=alerts[0] if alerts else None
    old_segment=trigger.get('segment_id') if trigger else None
    segment=state['segments'].get(old_segment,{})
    old_apis=[e for e in events if trigger and e['id']>trigger['id'] and e['kind']=='api_started'
              and state['segments'].get(e.get('segment_id'),{}).get('revision',trigger['revision'])<trigger['revision']]
    abandoned=[e for e in events if e['kind']=='diagnostic_api_delay_abandoned']
    dispatched=[e for e in events if e['kind']=='diagnostic_delayed_api_dispatched']
    regrasp=[e for e in events if trigger and e['id']>trigger['id'] and e['kind']=='api_result'
             and e.get('name')=='check_holding' and e.get('result',{}).get('status')=='verified']
    releases=[e for e in events if e['kind']=='placement_released']
    unsupported=[]
    for release in releases:
        checks=[e for e in events if e['id']<release['id'] and e.get('segment_id')==release.get('segment_id') and e['kind']=='placement_release_checked']
        if not checks or checks[-1].get('allowed') is not True or checks[-1]['evidence']['status']!='available':unsupported.append(release['id'])
    terminal=json.loads((trial/'state-at-terminal.json').read_text())
    assistance=terminal.get('assistance') or {}
    row.update(assistance_origin=assistance.get('origin'),assistance_message=assistance.get('message'),
        actuator_fault_count=len(starts),monitor_count=len(alerts),monitor_during_active_segment=bool(old_segment),
        active_cancelled_segment=segment.get('execution'),delayed_api_abandoned=len(abandoned),delayed_api_dispatched=len(dispatched),
        active_fault_coverage=bool(variant=='active' and len(starts)==1 and trigger and old_segment and confirmations and abandoned),
        late_old_revision_api_starts=len(old_apis),verified_regrasp_count=len(regrasp),
        releases_without_available_support=len(unsupported),
        source_unchanged=row['source_unchanged'],used_authoritative=state['used'],
        budget_ledger_matches=state['used']['decisions']==sum(e['kind']=='decision_admitted' for e in events)
          and state['used']['calls']==sum(e['kind']=='api_started' for e in events),
        fault_to_monitor_seconds=trigger['time']-starts[0]['time'] if trigger and starts else None,
        monitor_to_hold_seconds=confirmations[0]['latency_seconds'] if confirmations else None)
    rows.append(row)
output=dict(job=int(json.loads((root/'admission.json').read_text())['job_id']),cases=rows,
    scorer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    base_scorer_sha256=hashlib.sha256((project/'cap-runtime/tests/score_campaign.py').read_bytes()).hexdigest(),
    scope='Three natural tasks once each and one active-code delayed-RPC diagnostic, not a statistical comparison or moving-arm slip experiment')
(root/'independent-score.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps([{k:r.get(k) for k in ['variant','task','outcome','independent_success','public_declared_completion',
    'false_completion','active_fault_coverage','monitor_during_active_segment','active_cancelled_segment','delayed_api_abandoned',
    'delayed_api_dispatched','verified_regrasp_count','late_old_revision_api_starts','releases_without_available_support']} for r in rows]))
