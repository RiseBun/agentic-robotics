"""Public trace audit, performed only on both closed diagnostic episodes."""
import json
from pathlib import Path

root = Path(__file__).resolve().parent
rows = []
for admission in json.loads((root/'closed-admission.json').read_text()):
    directory = root/(admission['variant']+'-records')/admission['episode_id']
    if json.loads((directory/'episode.json').read_text())['status'] != 'closed':
        raise RuntimeError('Close all episodes before offline audit')
    events = [json.loads(line) for line in (directory/'events.jsonl').read_text().splitlines()]
    releases = [e for e in events if e['kind']=='placement_released']
    unsupported = []
    for release in releases:
        checks = [e for e in events if e['id'] < release['id'] and e['segment_id']==release['segment_id']
                  and e['kind']=='placement_release_checked']
        if not checks or not checks[-1].get('allowed') or checks[-1]['evidence'].get('status') != 'available':
            unsupported.append(release['id'])
    views = [e for e in events if e['kind']=='view_attempt']
    keys = [(e.get('acquisition_id'), tuple(e['target_xyz'])) for e in views]
    rows.append(dict(variant=admission['variant'], final_object_release_count=len(releases),
        releases_without_available_support=len(unsupported), unsupported_release_event_ids=unsupported,
        repeated_view_within_acquisition=len(keys)-len(set(keys)), view_attempts=len(views)))
(root/'release-audit.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
print(json.dumps(rows))
