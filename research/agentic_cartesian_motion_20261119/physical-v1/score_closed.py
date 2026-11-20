"""Score public TCP acceptance and private task outcome only after all cases close."""
import hashlib
import json
import sys
import tarfile
from pathlib import Path

root = Path(__file__).resolve().parent
sys.path.insert(0,str(root.parents[2]/'cap-runtime'))
from experiments.score_active_view import stable_outcome

if not (root/'records').exists():
    with tarfile.open(root/'offline-json.tar.gz') as archive:
        for member in archive.getmembers():
            if (root not in (root/member.name).resolve().parents or not member.isfile()
                    or member.name.split('/')[0] != 'records'):
                raise RuntimeError('Unexpected archive member')
        archive.extractall(root,filter='data')
records = root/'records'
rows = json.loads((records/'results.json').read_text())
protocol = json.loads((records/'protocol.json').read_text())
if [r['mode'] for r in rows] != protocol['cases']:
    raise RuntimeError('Require every frozen case')
admitted = []
for row in rows:
    directory = records/row['episode_id']
    state = json.loads((directory/'episode.json').read_text())
    events = [json.loads(s) for s in (directory/'events.jsonl').read_text().splitlines()]
    if state['status'] != 'closed' or events[-1]['kind'] != 'episode_closed':
        raise RuntimeError('All episodes must close before any truth is read')
    admitted.append((row,directory,state,events))
results = []
for row,directory,state,events in admitted:
    truth = [json.loads(s) for s in (directory/'evaluation-private.jsonl').read_text().splitlines()]
    task_case = row['mode'].startswith('cube_')
    motions = [e['result'] for e in events if e['kind']=='cartesian_motion_finished']
    phases = [phase for result in motions for phase in result['phases']]
    invalid = [p for p in phases if p['position_error_m']>p['position_tolerance_m']
               or p['orientation_error_rad']>p['orientation_tolerance_rad']]
    evaluation = stable_outcome(truth,events[-1]['time']-1.5,events[-1]['time']) if task_case else None
    declared = row.get('public_final',{}).get('status')=='verified'
    resets = [e['reset_count'] for e in events if e['kind']=='backend_configured']
    results.append(dict(mode=row['mode'],episode_id=row['episode_id'],driver_passed=row['passed'],
        error=row.get('error'),public_final=row.get('public_final',{}).get('status'),
        independent_task=evaluation, false_completion=declared and evaluation['status']=='failed' if task_case else None,
        completed_cartesian_commands=len(motions),completed_phases=len(phases),
        maximum_position_error_m=max((p['position_error_m'] for p in phases),default=None),
        maximum_orientation_error_rad=max((p['orientation_error_rad'] for p in phases),default=None),
        accepted_out_of_tolerance=len(invalid),
        ik_attempts=sum(e['kind']=='cartesian_ik_checked' for e in events),
        refinements=sum(e['kind']=='cartesian_ik_checked' and e['attempt']>1 for e in events),
        late_cartesian_events=row.get('late_cartesian_events'),late_api_starts=row.get('late_api_starts'),
        old_revision_rejected=row.get('old_revision_rejected'),
        reset_count=resets[0] if len(resets)==1 else resets,source_unchanged=row['source_unchanged'],
        budget_ledger_matches=state['used']['calls']==sum(e['kind']=='api_started' for e in events)
            and state['used']['decisions']==sum(e['kind']=='decision_admitted' for e in events),
        used=state['used']))
output = dict(cases=results,scope=protocol['scope'],
    scorer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
(root/'independent-score.json').write_text(json.dumps(output,indent=2),encoding='utf-8')
print(json.dumps(results))
