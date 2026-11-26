"""Offline scoring of all closed native model trials. Never imported by policy."""
import argparse
import hashlib
import json
from pathlib import Path
from experiments.score_active_view import stable_outcome


def read_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def score(root):
    campaign = json.loads((root/'campaign/campaign.json').read_text())
    admitted = []
    # Validate every scheduled close before opening any private evaluator file.
    for trial in campaign['plan']:
        folder = root/'campaign'/trial['trial_id']
        result = json.loads((folder/'result.json').read_text())
        physical = root/'physical'/result['episode_id']
        state = json.loads((physical/'episode.json').read_text())
        events = read_lines(physical/'events.jsonl')
        if (state['status'] != 'closed' or not events or events[-1]['kind'] != 'episode_closed'
                or not result.get('source_unchanged') or result.get('close_error')):
            raise RuntimeError('Incomplete, altered or unconfirmed trial; private scoring refused')
        admitted.append((trial, folder, result, physical, state, events))
    rows = []
    for trial, folder, result, physical, state, events in admitted:
        truth = read_lines(physical/'evaluation-private.jsonl')
        outcome = stable_outcome(truth, events[-1]['time']-1.5, events[-1]['time'])
        decisions = []
        for trace in (folder/'records').glob('*/workbench.jsonl'):
            decisions += [x['decision'] for x in read_lines(trace) if x['kind'] == 'decision']
        segments = list(state['segments'].values())
        rows.append(dict(task=trial['task'],seed=trial['seed'],outcome=result['outcome'],
            episode_id=state['id'],public_at_decision=result['public_at_decision']['status'],
            independent_physical=outcome,false_completion=result['outcome']=='completed' and outcome['status']=='failed',
            decision_sequence=[dict(decision=d['decision'],skill=(d.get('skill') or {}).get('name'),message=d.get('message')) for d in decisions],
            attempts=[dict(skill=(s.get('skill') or {}).get('name'),subgoal=s['subgoal'],execution=s['execution'],
                           node_effect=(s.get('node_verification') or {}).get('status'),error=s.get('error'),
                           feedback=(s.get('feedback') or {}).get('category')) for s in segments],
            release_checks=[dict(allowed=e['allowed'],camera=e.get('evidence',{}).get('camera_name'),reason=e.get('evidence',{}).get('reason'))
                            for e in events if e['kind']=='placement_release_checked'],
            reset_count=next((e['reset_count'] for e in events if e['kind']=='backend_configured'),None),
            budget_matches_events=state['used']['calls']==sum(e['kind']=='api_started' for e in events),
            used=state['used'],policy_seconds=result['policy_seconds']))
    return dict(scope='Development diagnosis; all planned episodes closed before truth access; no statistical claim',
                model=campaign['model'],source_digest=campaign['sources']['digest'],cases=rows,
                scorer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('root',type=Path)
    args=parser.parse_args()
    value=score(args.root)
    (args.root/'independent-summary.json').write_text(json.dumps(value,indent=2,ensure_ascii=False))
    print(json.dumps(value,ensure_ascii=False))
