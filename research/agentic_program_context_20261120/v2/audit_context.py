"""Audit saved model requests and public action records; never query the model."""
import json
from pathlib import Path

root=Path(__file__).resolve().parent
rows=[]
for file in (root/'normal-campaign').glob('*/records/*/workbench.jsonl'):
    for line in file.read_text(encoding='utf-8').splitlines():
        record=json.loads(line)
        if record['kind']!='model_request':
            continue
        body=record['body']
        text=body['input'][0]['content'][0]['text']
        context=json.loads(text)
        history=context.get('recent_attempts',[])
        rows.append(dict(trial_id=file.parents[2].name,scope=record.get('request_scope'),
            context_chars=len(text),history_chars=len(json.dumps(history)),
            has_raw_events=any('events' in s for s in history)))
summary=dict(requests=len(rows),max_context_chars=max((r['context_chars'] for r in rows),default=0),
    max_history_chars=max((r['history_chars'] for r in rows),default=0),
    raw_events_requests=sum(r['has_raw_events'] for r in rows),
    repair_requests=sum(r['scope']=='preflight_code_repair' for r in rows))
(root/'context-audit.json').write_text(json.dumps(dict(summary=summary,requests=rows,
    scope='Saved request bodies only; no causal benefit claim'),indent=2),encoding='utf-8')
print(json.dumps(summary))
