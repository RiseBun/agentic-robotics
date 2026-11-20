"""Independent post-close outcome check; never imported by the controller."""
import hashlib
import json
import sys
import tarfile
from pathlib import Path

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root.parents[2] / 'cap-runtime'))
from experiments.score_active_view import stable_outcome

records = root / 'records'
if not records.exists():
    with tarfile.open(root / 'closed-evidence.tar.gz') as archive:
        for item in archive.getmembers():
            target = (root / item.name).resolve()
            if root not in target.parents or not item.name.startswith('records/') or not (item.isfile() or item.isdir()):
                raise RuntimeError('Unexpected archive member')
        archive.extractall(root, filter='data')

def lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]

rows = json.loads((records / 'results.json').read_text())
if [r['mode'] for r in rows] != ['single', 'dual', 'dual_masked']:
    raise RuntimeError('Incomplete protocol')
for row in rows:
    episode = records / row['mode'] / row['episode_id']
    if json.loads((episode / 'episode.json').read_text())['status'] != 'closed':
        raise RuntimeError('Require every episode closed before accessing independent truth')

scored = []
for row in rows:
    episode = records / row['mode'] / row['episode_id']
    events = lines(episode / 'events.jsonl')
    closed = next(e['time'] for e in reversed(events) if e['kind'] == 'episode_closed')
    outcome = stable_outcome(lines(episode / 'evaluation-private.jsonl'), closed - 1.25, closed)
    public = row.get('verification', {}).get('status', 'unknown')
    scored.append(dict(mode=row['mode'], episode_id=row['episode_id'],
        physical_outcome=outcome, public_verification=public,
        false_completion=public == 'verified' and outcome['status'] == 'failed',
        reset_count=sum(e['kind'] == 'backend_configured' for e in events),
        support_before=row['before']['status'], support_after=row['after']['status'],
        source_unchanged=row['source_unchanged'], continuous_simulation=row['continuous_simulation'], used=row['used']))
output = dict(job=20758, task='cube_restack', seed=66, model_used=False, trials=scored,
    scope='Three fixed-controller conditions on one seen failing scene; not autonomous model performance or success rate',
    scorer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
(root / 'independent-score.json').write_text(json.dumps(output, indent=2))
print(json.dumps([dict(mode=r['mode'], physical=r['physical_outcome']['status'],
    public=r['public_verification'], resets=r['reset_count'], false_completion=r['false_completion']) for r in scored]))
