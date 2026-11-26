"""Offline fixed-controller camera comparison. Validate every close before truth I/O."""
import argparse
import hashlib
import json
from pathlib import Path
from experiments.score_unified_system import score as score_closed_trial


def read(path):
    return json.loads(path.read_text())


def lines(path):
    return [json.loads(x) for x in path.read_text().splitlines() if x]


def admit_all(root):
    plan=read(root/'plan.json')
    trials=[]
    for label, camera in plan['conditions']:
        folder=root/('fixed-'+label)
        protocol=read(folder/'protocol.json')
        rows=read(folder/'results.json')
        if (protocol['seed']!=plan['seed'] or protocol['tasks']!=[plan['task']]
                or protocol['secondary_camera']!=camera or len(rows)!=1 or rows[0]['task']!=plan['task']
                or protocol['runtime']['features']['support_second_view']!=camera):
            raise RuntimeError('Trial does not match frozen camera plan')
        expected_alternative=plan.get('alternate_recovery', {}).get(label, False)
        if protocol.get('alternate_recovery',False)!=expected_alternative:
            raise RuntimeError('Alternative recovery condition does not match frozen plan')
        episode=folder/rows[0]['episode_id']
        state=read(episode/'episode.json'); events=lines(episode/'events.jsonl')
        if state['status']!='closed' or not events or events[-1]['kind']!='episode_closed':
            raise RuntimeError('All planned camera episodes must close before private scoring')
        trials.append((label,camera,folder,protocol,events))
    signatures={(p['controller_sha256'],p['runtime']['source_digest'],p['runtime']['upstream_revision'],
                 p['runtime']['upstream_source_digest']) for _,_,_,p,_ in trials}
    if len(signatures)!=1 or len({x[0] for x in trials})!=len(trials):
        raise RuntimeError('Mixed source/controller or duplicated conditions')
    return plan,trials


def score(root):
    plan,trials=admit_all(root)
    # No evaluator is opened until every planned episode has passed the gate.
    results=[]
    for label,camera,folder,protocol,events in trials:
        scored=score_closed_trial(folder)['cases'][0]
        releases=[dict(event_id=e['id'],allowed=e['allowed'],evidence=e.get('evidence'),
                       holding_identity=e.get('holding_identity'))
                  for e in events if e['kind']=='placement_release_checked']
        results.append(dict(condition=label,camera=camera,**scored,release_checks=releases))
    return dict(scope=plan['scope'],seed=plan['seed'],task=plan['task'],cases=results,
                source=trials[0][3]['runtime'],controller_sha256=trials[0][3]['controller_sha256'],
                scorer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('root',type=Path);args=parser.parse_args()
    value=score(args.root)
    (args.root/'independent-summary.json').write_text(json.dumps(value,indent=2))
    print(json.dumps({**value,'cases':[{k:v for k,v in row.items() if k not in {'phases','release_checks'}} for row in value['cases']]}))
