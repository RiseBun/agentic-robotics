"""Post-run exposure audit. No policy changes and no current physics labels."""
import json,sys
from pathlib import Path
r=Path(sys.argv[1]);manifest=json.loads((r/'campaign.json').read_text());execution=json.loads((r/'execution.json').read_text())
if len(execution)!=len(manifest['runs']):raise ValueError('Audit waits for the complete frozen batch')
folders=[]
for row in execution:
 folder=r/f"{row['index']:02d}-{row['task']}-{row['seed']}-{row['condition']}"
 states=list((folder/'physical').glob('*/episode.json'))
 if len(states)!=1 or json.loads(states[0].read_text())['status']!='closed':raise ValueError('Every physical task must be closed')
 folders.append((row,folder))
rows=[]
for run,folder in folders:
 queries=[]
 for p in (folder/'workbench').glob('*/workbench.jsonl'):
  for line in p.read_text().splitlines():
   if not line:continue
   e=json.loads(line)
   if e['kind']=='tabletop_experience_retrieved':queries.append(e.get('result') or {})
 nonempty=[q for q in queries if q.get('items')]
 rows.append(dict(task=run['task'],seed=run['seed'],condition=run['condition'],queries=len(queries),nonempty_queries=len(nonempty),candidate_ids=sorted({c['id'] for q in nonempty for c in q['items']})))
summary=[]
for condition in sorted({x['condition'] for x in rows}):
 group=[x for x in rows if x['condition']==condition];summary.append(dict(condition=condition,runs=len(group),queries=sum(x['queries'] for x in group),nonempty_queries=sum(x['nonempty_queries'] for x in group),tasks_with_suggestions=sum(x['nonempty_queries']>0 for x in group)))
body=dict(scope='Recorded retrieval exposure only; this does not prove causal adoption of a suggestion',summary=summary,runs=rows)
(r/'experience-exposure.json').write_text(json.dumps(body,indent=2)+'\n');print(json.dumps(summary))
