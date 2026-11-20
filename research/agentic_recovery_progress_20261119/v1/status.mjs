import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = path.dirname(fileURLToPath(import.meta.url));
for (const variant of ['recoverable', 'occluded']) {
  const folder = path.join(root, 'campaign/stack-s70', variant, 'cube_stack-s70-r0');
  const result = path.join(folder, 'result.json');
  if (fs.existsSync(result)) {
    const row = JSON.parse(fs.readFileSync(result));
    console.log(JSON.stringify({ variant, outcome: row.outcome, public: row.public_at_decision?.status,
      episode_id: row.episode_id, source_unchanged: row.source_unchanged, seconds: row.policy_seconds }));
    continue;
  }
  const records = path.join(folder, 'records');
  const directory = fs.existsSync(records) ? fs.readdirSync(records, { withFileTypes: true }).find(e => e.isDirectory())?.name : null;
  const file = directory && path.join(records, directory, 'workbench.jsonl');
  if (!file || !fs.existsSync(file)) { console.log(JSON.stringify({ variant, status: 'not_yet_admitted' })); continue; }
  const rows = fs.readFileSync(file, 'utf8').trim().split('\n').flatMap(line => { try { return [JSON.parse(line)]; } catch { return []; } });
  const selected = rows.filter(r => ['decision', 'segment_feedback', 'assistance_requested', 'agent_error'].includes(r.kind)
    || r.kind === 'runtime_event' && ['public_holding_contradicted', 'public_holding_hold_confirmed'].includes(r.event?.kind));
  console.log(JSON.stringify({ variant, status: 'running', latest: selected.slice(-4).map(r => ({
    kind: r.kind, decision: r.decision?.decision, subgoal: r.decision?.subgoal,
    feedback: r.segment?.feedback?.category, event: r.event?.kind, origin: r.origin, error: r.error })) }));
}
