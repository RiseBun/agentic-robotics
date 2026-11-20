import hashlib
import json
import sys
import tarfile
from pathlib import Path

root=Path(__file__).resolve().parent
sys.path.insert(0,str(root.parents[2]/'cap-runtime/tests'))
from score_campaign import assess, jsonl

admitted=json.loads((root/'closed-admission.json').read_text())
if len(admitted)!=3:
    raise RuntimeError('Require all three original admissions')
if not (root/'normal-records').exists():
    with tarfile.open(root/'offline-json.tar.gz') as archive:
        for member in archive.getmembers():
            if (root not in (root/member.name).resolve().parents or not member.isfile()
                    or member.name.split('/')[0]!='normal-records'):
                raise RuntimeError('Unexpected archive member')
        archive.extractall(root,filter='data')
for row in admitted:
    if row.get('episode_id'):
        directory=root/'normal-records'/row['episode_id']
        if json.loads((directory/'episode.json').read_text())['status']!='closed' or jsonl(directory/'events.jsonl')[-1]['kind']!='episode_closed':
            raise RuntimeError('Close every episode before reading any truth')
campaign=root/'normal-campaign'
options=json.loads((root/'normal-options.json').read_text())
manifest=json.loads((campaign/'campaign.json').read_text())
if manifest['model']!=options['expectedModel']:
    raise RuntimeError('Model differs from frozen protocol')
rows=[]
for admission in admitted:
    trial=campaign/admission['trial_id']
    row=json.loads((trial/'result.json').read_text())
    if not row.get('episode_id'):
        rows.append(row)
        continue
    directory=root/'normal-records'/row['episode_id']
    events=jsonl(directory/'events.jsonl')
    state=json.loads((directory/'episode.json').read_text())
    row.update(assess(row,jsonl(directory/'evaluation-private.jsonl'),events,1.5))
    motions=[e['result'] for e in events if e['kind']=='cartesian_motion_finished']
    phases=[p for m in motions for p in m['phases']]
    row.update(completed_cartesian_commands=len(motions),
        maximum_position_error_m=max((p['position_error_m'] for p in phases),default=None),
        accepted_out_of_tolerance=sum(p['position_error_m']>p['position_tolerance_m']
            or p['orientation_error_rad']>p['orientation_tolerance_rad'] for p in phases),
        ik_refinements=sum(e['kind']=='cartesian_ik_checked' and e['attempt']>1 for e in events),
        motion_failures=[e['feedback']['evidence'].get('reason') for e in events
                         if e['kind']=='api_error' and e.get('feedback',{}).get('category')=='motion_target_not_reached'],
        used_authoritative=state['used'],
        budget_ledger_matches=state['used']['calls']==sum(e['kind']=='api_started' for e in events)
            and state['used']['decisions']==sum(e['kind']=='decision_admitted' for e in events))
    rows.append(row)
output=dict(cases=rows,scorer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    base_scorer_sha256=hashlib.sha256((root.parents[2]/'cap-runtime/tests/score_campaign.py').read_bytes()).hexdigest(),
    scope='Three natural model-driven tasks, seed 89, once each; no statistical or causal algorithm comparison')
(root/'independent-score.json').write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps([{k:r.get(k) for k in ['task','outcome','independent_success','public_declared_completion',
    'false_completion','accepted_out_of_tolerance','maximum_position_error_m','motion_failures']} for r in rows]))
