import hashlib
import json
import tarfile
from pathlib import Path
root=Path(__file__).resolve().parent
with tarfile.open(root/'assistance-json.tar.gz') as archive:
    for member in archive.getmembers():
        if root not in (root/member.name).resolve().parents or not member.isfile():raise RuntimeError('Bad archive member')
    archive.extractall(root,filter='data')
rows=json.loads((root/'results.json').read_text())
audits=[]
for row in rows:
    directory=root/row['episode_id']
    state=json.loads((directory/'episode.json').read_text())
    events=[json.loads(line) for line in (directory/'events.jsonl').read_text().splitlines()]
    if state['status']!='closed':raise RuntimeError('Close controls first')
    confirmed={e['hold_serial'] for e in events if e['kind']=='hold_confirmed'}
    requested={e['hold_serial'] for e in events if e['kind']=='cancel_requested'}
    config=next(e for e in events if e['kind']=='backend_configured')
    audit=dict(mode=row['mode'],episode_id=row['episode_id'],driver_passed=row['passed'],
        actual_hold_confirmations=len(confirmed),all_cancel_requests_confirmed=requested<=confirmed,
        public_api_starts=sum(e['kind']=='api_started' for e in events),
        goal=state['goal'],used=state['used'],reset_count=config['reset_count'],source_unchanged=row['source_unchanged'],
        scope='Scripted workbench protocol over actual CaP HTTP; no movement or LLM autonomy claim')
    audits.append(audit)
(root/'closed-audit.json').write_text(json.dumps(dict(cases=audits,scorer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()),indent=2),encoding='utf-8')
print(json.dumps(audits))
