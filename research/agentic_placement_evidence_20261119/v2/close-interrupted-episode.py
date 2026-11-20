"""Administrative close only; no action replay, reset, or private scoring."""
import argparse
import json
from pathlib import Path
import urllib.request

p = argparse.ArgumentParser()
p.add_argument('--port', type=int, required=True)
p.add_argument('--episode', required=True)
p.add_argument('--output', type=Path, required=True)
args = p.parse_args()
token = Path('/mnt/slurmfs-4090node1/homes/zchen897/projects/grad_robot_repair/agentic-mvp/runtime-token.txt').read_text().strip()

def request(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f'http://127.0.0.1:{args.port}'+path, data=data,
        headers={'Authorization': 'Bearer '+token, 'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.load(response)

route = '/episodes/'+args.episode
before = request(route)
held = request(route+'/cancel', {}) if before['status'] != 'closed' else {'stopped': True}
if not held.get('stopped'):
    raise RuntimeError('Protective hold was not confirmed; close not asserted')
closed = request(route+'/close', {}) if before['status'] != 'closed' else before
result = dict(episode_id=args.episode, replayed=False, before_status=before['status'],
              hold=held, closed_status=closed['status'], final_used=closed['used'])
args.output.write_text(json.dumps(result, indent=2))
print(json.dumps(result))
