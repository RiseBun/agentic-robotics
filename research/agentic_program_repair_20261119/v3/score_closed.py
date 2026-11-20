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
    with tarfile.open(root/'offline-json.tar.gz') as archive:
        for member in archive.getmembers():
            if (root not in (root/member.name).resolve().parents or not member.isfile()
                    or member.name.split('/')[0]!='repair-records'):
                raise RuntimeError('Unexpected archive member')
        archive.extractall(root,filter='data')
preparation=json.loads((root/'repair-records/preparation.json').read_text())
directory=root/'repair-records'/preparation['episode_id']
state=json.loads((directory/'episode.json').read_text())
events=jsonl(directory/'events.jsonl')
if state['status']!='closed' or events[-1]['kind']!='episode_closed':
    raise RuntimeError('Original episode must close before reading truth')
result=json.loads((root/'repair-campaign/unbound_name/result.json').read_text())
workbench=next((root/'repair-campaign/unbound_name/records').glob('*/workbench.jsonl'))
rows=jsonl(workbench)
decisions=[r['decision'] for r in rows if r['kind']=='decision']
handoff=next(e for e in events if e['kind']=='diagnostic_model_handoff_ready')
after=[e for e in events if e['id']>handoff['id']]
first=next((e['segment'] for e in after if e['kind']=='segment_finished'),{})
invalid=next(s for s in state['segments'].values() if s['subgoal']=='invalid-unbound_name')
scores=assess(result,jsonl(directory/'evaluation-private.jsonl'),events,1.5)
terminal=json.loads((root/'repair-campaign/unbound_name/state-at-terminal.json').read_text())
output=dict(episode_id=state['id'],outcome=result['outcome'],independent=scores,
    invalid_preparation_api_count=len(invalid['api_attempts']),diagnostic=invalid['feedback']['evidence'],
    first_model_decision=decisions[0] if decisions else None,
    first_model_segment_execution=first.get('execution'),
    first_model_segment_feedback=first.get('feedback'),
    assistance=terminal.get('assistance'),
    unchanged_code_returned=bool(decisions and decisions[0].get('code')==invalid['code']),
    invalid_programs_after_handoff=sum(e['kind']=='segment_finished' and e['segment'].get('feedback',{}).get('category')=='invalid_program' for e in after),
    source_unchanged=result['source_unchanged'],used=state['used'],
    reset_count=next(e['reset_count'] for e in events if e['kind']=='backend_configured'),
    budget_ledger_matches=state['used']['calls']==sum(e['kind']=='api_started' for e in events)
      and state['used']['decisions']==sum(e['kind']=='decision_admitted' for e in events),
    scorer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    scope='One prepared unbound-name failure followed by autonomous repair/original-goal execution; not natural success rate or causal comparison')
(root/'independent-score.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:output[k] for k in ['outcome','independent','first_model_segment_execution','unchanged_code_returned','invalid_programs_after_handoff','source_unchanged','used']}))
