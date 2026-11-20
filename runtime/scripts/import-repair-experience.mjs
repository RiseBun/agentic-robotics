// Explicit offline admission of already scored, closed research records.
// Never reads evaluation-private.jsonl, model keys, or historical executable code.
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { deriveRepair, digest, ExperienceStore, recoveryContract } from '../robot/experience.mjs';
const hashBytes = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const read = async file => JSON.parse(await fs.readFile(file, 'utf8'));

export async function importRepairs(records, storeRoot, scorer) {
  const protocol = await read(path.join(records, 'campaign/protocol.json'));
  const reportBytes = await fs.readFile(path.join(records, 'score.json'));
  const report = JSON.parse(reportBytes);
  if (digest(protocol.sources.files) !== protocol.sources.digest || report.sources !== protocol.sources.digest
      || hashBytes(await fs.readFile(scorer)) !== report.scorer_sha256) throw new Error('Unmatched frozen source or independent scorer');
  for (const file of protocol.sources.files) {
    if (!/^(runtime\/(robot|model-providers)|cap-runtime\/agentic_cap)\/[A-Za-z0-9_.-]+$/.test(file.path)
        || hashBytes(await fs.readFile(path.join(records, 'source', file.path))) !== file.sha256) {
      throw new Error('Frozen source file differs: '+file.path);
    }
  }
  const provenance = { backend: 'cap', source_digest: hashBytes(Buffer.from(protocol.sources.files
    .filter(f => f.path.startsWith('cap-runtime/agentic_cap/')).sort((a,b) => a.path.localeCompare(b.path, 'en'))
    .map(f => f.path.slice('cap-runtime/'.length)+'\0'+f.sha256+'\n').join(''))),
    recovery_contract: recoveryContract(protocol.sources.files) };
  const closed = [];
  // First check every episode closed, including failures, before consulting scoring rows.
  for (const entry of protocol.cases) {
    if (!/^[a-f0-9]{32}$/.test(entry.episode_id) || !['retreat_recovers', 'persistent_unknown'].includes(entry.mode)) {
      throw new Error('Unsupported frozen diagnosis case');
    }
    const directory = path.join(records, entry.mode+'-records', entry.episode_id);
    const episode = await read(path.join(directory, 'episode.json'));
    const result = await read(path.join(records, 'campaign', entry.mode, 'result.json'));
    const events = (await fs.readFile(path.join(directory, 'events.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse);
    if (episode.id !== entry.episode_id || episode.status !== 'closed' || events.at(-1)?.kind !== 'episode_closed'
        || result.episode_id !== episode.id || result.source_unchanged !== true || result.close_error) {
      throw new Error('Every original episode must be closed and source-stable');
    }
    closed.push({ entry, trace: { protocol: 1, episode, events, provenance } });
  }
  const store = new ExperienceStore(storeRoot), admitted = [], skipped = [];
  for (const { entry, trace } of closed) {
    const row = report.rows.find(r => r.episode_id === entry.episode_id && r.mode === entry.mode);
    if (!row || row.independent_physical?.status !== 'verified' || row.public_at_decision !== 'verified'
        || row.false_completion !== false || row.reset_count !== 1 || !deriveRepair(trace)) {
      skipped.push(entry.episode_id); continue;
    }
    const candidate = await store.propose(trace);
    await store.validate(candidate.id, { episode_id: trace.episode.id, trace_sha256: candidate.source.trace_sha256,
      public_verified: true, independent_verified: true, scorer_sha256: report.scorer_sha256,
      report_sha256: hashBytes(reportBytes) });
    admitted.push({ id: candidate.id, source_episode_id: trace.episode.id, source_task: trace.episode.task });
  }
  return { admitted, skipped, source_manifest: protocol.sources.digest,
    scope: 'Recognizes one bounded repair pattern; not general skill induction or evidence of transfer benefit.' };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [records, store] = process.argv.slice(2);
  if (!records || !store) throw new Error('Usage: node import-repair-experience.mjs CLOSED_DIAGNOSIS VALIDATED_STORE');
  const scorer = fileURLToPath(new URL('../../cap-runtime/experiments/score_retreat_model.py', import.meta.url));
  console.log(JSON.stringify(await importRepairs(path.resolve(records), path.resolve(store), scorer), null, 2));
}
