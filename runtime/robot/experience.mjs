// Structured historical cases, never executable code or present-state facts.
import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';

export const CONTRACT_FILES = ['active_evidence.py', 'cap_backend.py', 'cartesian_motion.py', 'core.py', 'feedback.py', 'holding.py', 'holding_watchdog.py', 'interpreter.py', 'public_views.py',
  'placement_evidence.py', 'public_sensors.py', 'safe_retreat.py'];
export const digest = value => crypto.createHash('sha256').update(typeof value === 'string' ? value : JSON.stringify(value)).digest('hex');
export function recoveryContract(files) {
  const rows = CONTRACT_FILES.map(name => {
    const entry = files.find(f => f.path === 'cap-runtime/agentic_cap/'+name);
    if (!entry || !/^[a-f0-9]{64}$/.test(entry.sha256)) throw new Error('Missing recovery contract source: '+name);
    return name+'\0'+entry.sha256+'\n';
  });
  return digest('bounded-cube-recovery-v5\n'+rows.join(''));
}
const names = ['red cube', 'green cube'];
const sha = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const finite = (a, n) => Array.isArray(a) && a.length === n && a.every(Number.isFinite);
const latest = (rows, predicate) => rows.findLast(predicate);

export function deriveRepair(trace) {
  const { episode, events, provenance } = trace || {};
  if (trace?.protocol !== 1 || episode?.status !== 'closed' || provenance?.backend !== 'cap'
      || !sha(provenance.recovery_contract) || !sha(provenance.source_digest)
      || !['cube_stack', 'cube_restack'].includes(episode.task) || !Array.isArray(events) || !events.length
      || events.some((e, i) => e.id !== i+1 || e.episode_id !== episode.id)
      || events.at(-1).kind !== 'episode_closed') return null;
  const on = episode.goal?.on;
  if (!Array.isArray(on) || on.length !== 2 || on[0] === on[1] || on.some(n => !names.includes(n))
      || episode.goal.empty_gripper !== true || events.some(e => e.kind === 'goal_changed')) return null;
  const finish = latest(events, e => e.kind === 'finish_checked');
  if (finish?.result?.status !== 'verified' || events.some(e => e.id > finish.id && e.kind === 'api_started')) return null;
  const blocked = events.find(e => e.kind === 'placement_release_checked' && e.allowed === false
    && e.evidence?.status === 'unknown');
  const retreat = events.find(e => blocked && e.id > blocked.id && e.id < finish.id
    && e.kind === 'placement_retreat_checked' && e.allowed === true && e.retreat?.status === 'available');
  if (!retreat) return null;
  const start = latest(events, e => e.kind === 'api_started' && e.name === 'goto_pose'
    && e.segment_id === retreat.segment_id && e.id < retreat.id);
  const done = events.find(e => e.kind === 'api_result' && e.call_id === start?.call_id && e.id > retreat.id);
  const near = (a, b, n) => finite(a, n) && finite(b, n) && a.every((v, i) => Math.abs(v-b[i]) <= 1e-9);
  if (!start || !done || !near(start.args?.[0], retreat.retreat.target_xyz, 3)
      || !near(start.args?.[1], retreat.retreat.target_wxyz, 4)
      || done.error || done.result?.status === 'failed' || done.result?.status === 'unknown'
      || (start.kwargs?.z_approach ?? 0) !== 0) return null;
  const support = events.find(e => e.id > done.id && e.id < finish.id
    && ((e.kind === 'placement_motion_checked' && e.evidence?.status === 'available')
      || (e.kind === 'evidence_acquisition_finished' && e.support === on[1] && e.status === 'available')));
  const held = events.find(e => e.id > done.id && e.id < finish.id
    && e.kind === 'api_result' && e.name === 'check_holding' && e.result?.status === 'verified'
    && events.some(a => a.kind === 'api_started' && a.call_id === e.call_id && a.args?.[0] === on[0]));
  if (!support || !held) return null;
  const traceHash = digest(trace);
  const body = { schema: 1, kind: 'recorded_vertical_retreat',
    source: { episode_id: episode.id, task: episode.task, goal: episode.goal,
      source_digest: provenance.source_digest, recovery_contract: provenance.recovery_contract,
      trace_sha256: traceHash, witness_ids: [blocked.id, retreat.id, done.id, held.id, support.id, finish.id] },
    conditions: { backend: 'cap', frame: 'cap_robot_base', on,
      required_current_fact: 'fresh unknown support and available recorded vertical retreat',
      transfer_scope: 'bounded red/green cube stack and restack; same recovery contract only' },
    actions: [
      { api: 'goto_pose', bindings: { xyz: 'latest_observation.recovery_options.vertical_retreat.target_xyz',
        wxyz: 'latest_observation.recovery_options.vertical_retreat.target_wxyz' }, z_approach: 0 },
      { api: 'check_holding', bindings: { object_name: 'current_goal.on[0]' } },
      { decision: 'observe', purpose: 'Use the new public support evidence before another placement decision' }
    ],
    limitation: 'One historical case. This plan must be rebound to current observations and reverified; no success guarantee.' };
  return { id: digest(body), ...body };
}

export function matchRepair(candidate, query) {
  const { observation: o, episode: e, provenance: p } = query;
  if (!candidate || candidate.id !== digest(Object.fromEntries(Object.entries(candidate).filter(([k]) => k !== 'id')))
      || candidate.schema !== 1 || candidate.kind !== 'recorded_vertical_retreat'
      || !candidate.source || !candidate.conditions || !Array.isArray(candidate.actions)) return 'invalid_candidate';
  if (candidate.source.episode_id === e?.id) return 'same_episode';
  if (p?.backend !== 'cap' || p.recovery_contract !== candidate.source.recovery_contract) return 'incompatible_runtime';
  if (!['cube_stack', 'cube_restack'].includes(e?.task) || !same(e.goal?.on, candidate.conditions.on)
      || e.goal?.empty_gripper !== true || e.goal?.holding
      || !same(o?.goal, e.goal) || o?.revision !== e.revision) return 'different_goal_or_revision';
  if (o?.backend !== 'cap' || o.coordinate_convention?.frame !== candidate.conditions.frame) return 'different_sensor_frame';
  const age = query.observation_age_upper_seconds;
  if (!Number.isFinite(o.captured_at) || !Number.isFinite(o.age_seconds) || o.age_seconds < 0
      || !Number.isFinite(age) || age < o.age_seconds || age > 1) return 'stale_observation';
  const recovery = o.recovery_options?.vertical_retreat;
  if (o.support_evidence?.[e.goal.on[1]]?.status !== 'unknown' || o.last_gripper_command !== 'closed'
      || recovery?.status !== 'available' || !finite(recovery.target_xyz, 3) || !finite(recovery.target_wxyz, 4)) return 'trigger_not_present';
  if (e.status !== 'ready' || e.active_segment) return 'episode_unavailable';
  if (![e.used?.calls, e.limits?.calls, e.used?.decisions, e.limits?.decisions,
    e.used?.motion_steps, e.limits?.motion_steps, e.used?.wall_seconds, e.limits?.wall_seconds].every(Number.isFinite)
      || e.used.calls+2 > e.limits.calls || e.used.decisions > e.limits.decisions
      || e.used.motion_steps >= e.limits.motion_steps || e.used.wall_seconds >= e.limits.wall_seconds) return 'insufficient_budget';
  return null;
}

async function readJson(file) {
  try {
    if ((await fs.stat(file)).size > 64_000) throw new Error('Experience record too large');
    return JSON.parse(await fs.readFile(file, 'utf8'));
  } catch (e) { if (e.code === 'ENOENT') return null; throw e; }
}
export class ExperienceStore {
  constructor(root) { this.root = root; }
  file(type, id) {
    if (!sha(id)) throw new Error('Invalid experience ID');
    return path.join(this.root, type, id+'.json');
  }
  async immutable(type, id, value) {
    const file = this.file(type, id);
    await fs.mkdir(path.dirname(file), { recursive: true });
    const temp = file+'.'+crypto.randomUUID()+'.tmp';
    try {
      await fs.writeFile(temp, JSON.stringify(value), { flag: 'wx' });
      try { await fs.link(temp, file); }
      catch (e) { if (e.code !== 'EEXIST' || !same(await readJson(file), value)) throw e; }
    } finally { await fs.unlink(temp).catch(e => { if (e.code !== 'ENOENT') throw e; }); }
  }
  async propose(trace) {
    const candidate = deriveRepair(trace);
    if (!candidate) return null;
    await this.immutable('candidates', candidate.id, candidate);
    return candidate;
  }
  async validate(id, receipt) {
    const candidate = await readJson(this.file('candidates', id));
    if (!candidate || receipt?.episode_id !== candidate.source.episode_id
        || receipt.trace_sha256 !== candidate.source.trace_sha256 || receipt.public_verified !== true
        || receipt.independent_verified !== true || !sha(receipt.scorer_sha256) || !sha(receipt.report_sha256)) {
      throw new Error('Independent validation receipt does not match the public repair');
    }
    await this.immutable('validated', id, { schema: 1, candidate_sha256: digest(candidate), receipt });
  }
  async revoke(id, reason = 'operator_review') {
    if (!['operator_review', 'verified_regression', 'incompatible_evidence'].includes(reason)) throw new Error('Invalid revocation reason');
    await this.immutable('revoked', id, { schema: 1, reason });
  }
  async retrieve(query) {
    const directory = path.join(this.root, 'validated');
    const files = await fs.readdir(directory).catch(e => { if (e.code === 'ENOENT') return []; throw e; });
    const matches = [], rejected = [], manifest = [];
    for (const file of files.sort().slice(0, 256)) {
      const id = file.replace(/\.json$/, '');
      if (!sha(id)) continue;
      let candidate, approval, revoked;
      try {
        [candidate, approval, revoked] = await Promise.all([
          readJson(this.file('candidates', id)), readJson(this.file('validated', id)), readJson(this.file('revoked', id))]);
      } catch {
        rejected.push({ id, reason: 'unreadable_record' }); manifest.push({ id, unreadable: true }); continue;
      }
      manifest.push({ id, candidate: digest(candidate), approval: digest(approval), revoked: digest(revoked) });
      const receipt = approval?.receipt;
      const valid = approval?.schema === 1 && candidate && approval.candidate_sha256 === digest(candidate)
        && receipt?.episode_id === candidate.source?.episode_id && receipt.trace_sha256 === candidate.source?.trace_sha256
        && receipt.public_verified === true && receipt.independent_verified === true
        && sha(receipt.scorer_sha256) && sha(receipt.report_sha256);
      const reason = revoked ? 'revoked' : !valid ? 'missing_or_changed_validation' : matchRepair(candidate, query);
      if (reason) { rejected.push({ id, reason }); continue; }
      if (matches.some(m => m.kind === candidate.kind)) continue;
      matches.push({ id, kind: candidate.kind, source_episode_id: candidate.source.episode_id,
        source_task: candidate.source.task, witness_event_ids: candidate.source.witness_ids,
        actions: candidate.actions, limitation: candidate.limitation });
    }
    return { matches: matches.slice(0, 2), rejected, corpus_sha256: digest(manifest),
      usage: 'Historical hints only. Bind to current state; Runtime guards and final verification remain mandatory.' };
  }
}
