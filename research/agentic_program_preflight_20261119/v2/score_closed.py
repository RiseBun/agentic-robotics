"""Closed-only physical scoring and audit of the rejected segment's zero effects."""
import hashlib
import json
import sys
import tarfile
from pathlib import Path

root = Path(__file__).resolve().parent
project = root.parents[2]
sys.path.insert(0, str(project/'cap-runtime/tests'))
from score_campaign import assess, jsonl

if not (root/'records').exists():
    with tarfile.open(root/'offline-json.tar.gz') as archive:
        for member in archive.getmembers():
            target = (root/member.name).resolve()
            if root not in target.parents or not member.isfile() or member.name.split('/')[0] != 'records':
                raise RuntimeError('Unexpected archive member')
        archive.extractall(root, filter='data')
result = json.loads((root/'records/result.json').read_text())
directory = root/'records'/result['episode_id']
state = json.loads((directory/'episode.json').read_text())
events = jsonl(directory/'events.jsonl')
if state['status'] != 'closed' or not any(e['kind'] == 'episode_closed' for e in events):
    raise RuntimeError('Close before reading private outcomes')
bad_id = result['bad_segment']['id']
bad_calls = [e for e in events if e['kind'] == 'api_started' and e.get('segment_id') == bad_id]
physical = assess(dict(outcome='completed' if result['passed'] else 'needs_attention'),
                  jsonl(directory/'evaluation-private.jsonl'), events, settle_seconds=1.25)
output = dict(job=int(json.loads((root/'admission.json').read_text())['job_id']), episode_id=result['episode_id'], **physical,
    public_final=result['public_final']['status'], source_unchanged=result['source_unchanged'],
    invalid_segment_execution=result['bad_segment']['execution'],
    invalid_segment_api_calls=len(bad_calls), invalid_segment_api_budget_delta=result['bad_api_delta'],
    corrected_segment_execution=result['corrected_segment']['execution'], used=state['used'],
    scorer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    base_scorer_sha256=hashlib.sha256((project/'cap-runtime/tests/score_campaign.py').read_bytes()).hexdigest(),
    scope='One fixed-controller cube_lifting/71 regression; not an LLM recovery or success-rate experiment')
(root/'independent-score.json').write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(output, ensure_ascii=False))
