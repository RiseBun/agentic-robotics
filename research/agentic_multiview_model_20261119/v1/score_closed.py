"""No policy import. Score only after all admitted model trials have closed."""
import hashlib
import json
import sys
import tarfile
from pathlib import Path

root = Path(__file__).resolve().parent
project = root.parents[2]
sys.path.insert(0, str(project / 'cap-runtime/tests'))
from score_campaign import assess, jsonl

host = root / 'campaign'
if not (host / 'host-result.json').is_file(): raise RuntimeError('Model campaign not finished')
protocol = json.loads((host / 'paired-campaign.json').read_text())
if len(protocol['schedule']) != 8: raise RuntimeError('Unexpected frozen schedule')
if not (root / 'baseline-records').exists():
    with tarfile.open(root / 'offline-json.tar.gz') as archive:
        for item in archive.getmembers():
            target = (root / item.name).resolve()
            if root not in target.parents or item.name.split('/')[0] not in {'baseline-records', 'candidate-records'} or not item.isfile():
                raise RuntimeError('Unexpected archive member')
        archive.extractall(root, filter='data')

admitted = []
for case in protocol['schedule']:
    folder = host / case['pair_id'] / case['variant']
    manifest = json.loads((folder / 'campaign.json').read_text())
    if len(manifest['plan']) != 1: raise RuntimeError('Unexpected per-condition admission')
    trial = folder / manifest['plan'][0]['trial_id']
    row = json.loads((trial / 'result.json').read_text())
    episode = root / (case['variant'] + '-records') / row.get('episode_id', 'missing')
    if row.get('episode_id') and json.loads((episode/'episode.json').read_text())['status'] != 'closed':
        raise RuntimeError('Close every episode before independent scoring')
    admitted.append((case, trial, row, episode))

def contexts(value):
    if isinstance(value, dict):
        for child in value.values(): yield from contexts(child)
    elif isinstance(value, list):
        for child in value: yield from contexts(child)
    elif isinstance(value, str) and value.startswith('{'):
        try: parsed = json.loads(value)
        except (ValueError, TypeError): return
        if isinstance(parsed, dict) and 'latest_observation' in parsed: yield parsed

scored = []
for case, trial, row, episode in admitted:
    events = jsonl(episode/'events.jsonl')
    independent = assess(row, jsonl(episode/'evaluation-private.jsonl'), events, settle_seconds=1.25)
    requests = []
    workbench = [e for file in (trial/'records').glob('*/workbench.jsonl') for e in jsonl(file)]
    for event in workbench:
        if event['kind'] != 'model_request': continue
        for context in contexts(event['body']):
            observation = context.get('latest_observation') or {}
            support = observation.get('support_evidence', {}).get('green cube', {})
            requests.append(dict(mode=context.get('mode'), evidence_ref=observation.get('evidence_ref'),
                support_status=support.get('status'), support_source=support.get('camera_name'),
                scene_image_sources=context.get('scene_image_sources', [])))
    api_calls = [e for e in events if e['kind'] == 'api_started']
    release_checks = []
    for event in events:
        if event['kind'] != 'placement_release_checked': continue
        evidence = event['evidence']
        release_checks.append(dict(event_id=event['id'], segment_id=event['segment_id'], allowed=event['allowed'],
            status=evidence['status'], camera_name=evidence.get('camera_name'),
            horizontal_span_ratio=evidence.get('horizontal_span_ratio'),
            evidence_ref=evidence.get('evidence_ref'), evidence_refs=evidence.get('evidence_refs')))
    row.update(case, **independent, model_inputs=requests,
        dual_image_model_requests=sum(len(r['scene_image_sources']) == 2 for r in requests),
        fused_support_model_inputs=sum(r['support_source'] == 'public_rgbd_union' and r['support_status'] == 'available' for r in requests),
        inspect_support_calls=sum(e['name'] == 'inspect_support' for e in api_calls),
        inspect_placement_calls=sum(e['name'] == 'inspect_placement' for e in api_calls),
        successful_open_gripper_calls=sum(e['kind'] == 'api_result' and e['name'] == 'open_gripper' for e in events),
        recorded_final_object_releases=sum(e['kind'] == 'placement_released' for e in events),
        placement_release_checks=release_checks,
        fusion_allowed_release_checks=sum(c['allowed'] and c['camera_name'] == 'public_rgbd_union' for c in release_checks),
        additional_user_inputs=max(0, sum(e['kind'] == 'user_input' for e in workbench)-1))
    scored.append(row)
groups = {}
for variant in ['baseline', 'candidate']:
    rows = [r for r in scored if r['variant'] == variant]
    groups[variant] = dict(trials=len(rows), physical_success=sum(r['independent_success'] is True for r in rows),
        public_completion=sum(r['public_declared_completion'] for r in rows),
        false_completion=sum(r['false_completion'] is True for r in rows),
        unknown_physical=sum(r['independent_success'] is None for r in rows),
        infrastructure_errors=sum(r['outcome'] in {'infrastructure_error', 'interrupted'} for r in rows),
        dual_image_model_requests=sum(r['dual_image_model_requests'] for r in rows),
        fused_support_model_inputs=sum(r['fused_support_model_inputs'] for r in rows),
        fusion_allowed_release_checks=sum(r['fusion_allowed_release_checks'] for r in rows))
output = dict(groups=groups, trials=scored, source_variants=protocol['variants'], model=protocol['model'],
    scope='Eight original natural-task trials; two seeds, no significance or cross-hardware claim',
    scorer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    base_scorer_sha256=hashlib.sha256((project/'cap-runtime/tests/score_campaign.py').read_bytes()).hexdigest())
(root/'independent-score.json').write_text(json.dumps(output, ensure_ascii=False, indent=2))
print(json.dumps(groups))
