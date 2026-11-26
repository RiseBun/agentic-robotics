import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { deriveRepair, digest, ExperienceStore, matchRepair, recoveryContract, CONTRACT_FILES } from './experience.mjs';
import { RobotCoordinator } from './coordinator.mjs';

export function repairTrace() {
  const goal = { on: ['red cube', 'green cube'], empty_gripper: true };
  const pose = { target_xyz: [.6, -.1, .15], target_wxyz: [0, 0, 1, 0], status: 'available' };
  const events = [
    { kind: 'placement_release_checked', allowed: false, evidence: { status: 'unknown' } },
    { kind: 'api_started', name: 'goto_pose', call_id: 'up', segment_id: 's', args: [[...pose.target_xyz], [...pose.target_wxyz]], kwargs: {} },
    { kind: 'placement_retreat_checked', allowed: true, segment_id: 's', retreat: pose },
    { kind: 'api_result', name: 'goto_pose', call_id: 'up', result: null },
    { kind: 'api_started', name: 'check_holding', call_id: 'held', args: ['red cube'] },
    { kind: 'api_result', name: 'check_holding', call_id: 'held', result: { status: 'verified' } },
    { kind: 'placement_motion_checked', evidence: { status: 'available' } },
    { kind: 'finish_checked', result: { status: 'verified' } },
    { kind: 'episode_closed' }
  ].map((e, i) => ({ ...e, id: i+1, episode_id: 'old' }));
  return { protocol: 1, episode: { id: 'old', status: 'closed', task: 'cube_stack', goal }, events,
    provenance: { backend: 'cap', source_digest: 'a'.repeat(64), recovery_contract: 'b'.repeat(64) } };
}
function query() {
  const goal = repairTrace().episode.goal;
  return { episode: { id: 'new', task: 'cube_restack', goal, revision: 9, status: 'ready', active_segment: null,
    used: { calls: 6, decisions: 4, motion_steps: 100, wall_seconds: 30 },
    limits: { calls: 40, decisions: 12, motion_steps: 1500, wall_seconds: 300 } },
    observation: { goal, revision: 9, backend: 'cap', coordinate_convention: { frame: 'cap_robot_base' },
      captured_at: Date.now()/1000-.01, age_seconds: .01, last_gripper_command: 'closed', support_evidence: { 'green cube': { status: 'unknown' } },
      recovery_options: { vertical_retreat: { status: 'available', target_xyz: [.7, .2, .13], target_wxyz: [0, 0, 1, 0] } } },
    provenance: repairTrace().provenance, observation_age_upper_seconds: .05 };
}
function receipt(c) { return { episode_id: c.source.episode_id, trace_sha256: c.source.trace_sha256,
  public_verified: true, independent_verified: true, scorer_sha256: 'c'.repeat(64), report_sha256: 'd'.repeat(64) }; }
async function storeFixture(t) {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'robot-experience-'));
  t.after(() => fs.rm(dir, { recursive: true, force: true }));
  return new ExperienceStore(dir);
}

test('extracts only a complete successful public repair and removes old geometry and executable code', () => {
  const trace = repairTrace();
  trace.events[1].code = 'untrusted historical Python';
  const c = deriveRepair(trace);
  assert.ok(c);
  assert.deepEqual(c.source.witness_ids, [1, 3, 4, 6, 7, 8]);
  assert.ok(!JSON.stringify(c).includes('untrusted'));
  assert.ok(!JSON.stringify(c).includes('[0.6,-0.1,0.15]'));
  assert.equal(matchRepair(c, query()), null); // stack -> restack, different live pose
  for (const mutate of [
    t => t.episode.status = 'ready', t => t.events.pop(), t => t.events[4].id = 99,
    t => t.events[5].result.status = 'unknown', t => t.events[7].result.status = 'unknown',
    t => t.events[0].allowed = true, t => t.events[2].retreat.status = 'unknown',
    t => t.events[3].result = { status: 'failed' }, t => t.events[6].kind = 'goal_changed',
    t => t.events[1].args[0][0] += .01,
  ]) {
    const changed = structuredClone(trace); mutate(changed);
    assert.equal(deriveRepair(changed), null);
  }
});

test('rejects stale, mismatched, exhausted or unavailable current evidence', () => {
  const c = deriveRepair(repairTrace());
  const cases = [
    [q => q.episode.id = 'old', 'same_episode'],
    [q => q.provenance.recovery_contract = 'c'.repeat(64), 'incompatible_runtime'],
    [q => q.observation.revision++, 'different_goal_or_revision'],
    [q => q.episode.goal = { holding: 'red cube' }, 'different_goal_or_revision'],
    [q => q.observation.coordinate_convention.frame = 'world', 'different_sensor_frame'],
    [q => q.observation_age_upper_seconds = 2, 'stale_observation'],
    [q => q.observation.age_seconds = -1, 'stale_observation'],
    [q => q.observation_age_upper_seconds = null, 'stale_observation'],
    [q => q.observation.support_evidence['green cube'].status = 'available', 'trigger_not_present'],
    [q => q.observation.recovery_options.vertical_retreat.status = 'unknown', 'trigger_not_present'],
    [q => q.observation.last_gripper_command = 'open', 'trigger_not_present'],
    [q => q.episode.status = 'closed', 'episode_unavailable'],
    [q => q.episode.used.calls = 39, 'insufficient_budget'],
    [q => delete q.episode.used, 'insufficient_budget'],
    [q => q.episode.used.wall_seconds = 300, 'insufficient_budget'],
  ];
  for (const [mutate, reason] of cases) {
    const q = query(); mutate(q); assert.equal(matchRepair(c, q), reason);
  }
  const clockSkew = query(); clockSkew.observation.captured_at -= 3600;
  assert.equal(matchRepair(c, clockSkew), null);
});

test('candidate needs independent validation; immutable deduplication, audit, revocation and tampering', async t => {
  const store = await storeFixture(t);
  const c = await store.propose(repairTrace());
  assert.deepEqual(await store.propose(repairTrace()), c);
  assert.equal((await store.retrieve(query())).matches.length, 0);
  await assert.rejects(store.validate(c.id, { ...receipt(c), independent_verified: false }));
  await store.validate(c.id, receipt(c));
  const result = await store.retrieve(query());
  assert.equal(result.matches[0].id, c.id);
  assert.equal(result.matches[0].source_task, 'cube_stack');
  assert.ok(!JSON.stringify(result.matches).includes('report_sha256'));
  assert.match(result.corpus_sha256, /^[a-f0-9]{64}$/);
  await store.revoke(c.id, 'verified_regression');
  const revoked = await store.retrieve(query());
  assert.equal(revoked.matches.length, 0);
  assert.equal(revoked.rejected[0].reason, 'revoked');
  assert.notEqual(revoked.corpus_sha256, result.corpus_sha256);
});

test('corrupt or changed records fail closed without stopping the task', async t => {
  const store = await storeFixture(t), c = await store.propose(repairTrace());
  await store.validate(c.id, receipt(c));
  await fs.writeFile(store.file('candidates', c.id), JSON.stringify({ ...c, actions: [] }));
  assert.equal((await store.retrieve(query())).rejected[0].reason, 'missing_or_changed_validation');
  await fs.writeFile(store.file('validated', c.id), '{incomplete');
  assert.equal((await store.retrieve(query())).rejected[0].reason, 'unreadable_record');
  await assert.rejects(store.propose(repairTrace()));
  await assert.rejects(store.revoke('../escape'));
});

test('contract is deterministic, requires every semantic dependency and changes with any dependency', () => {
  const files = CONTRACT_FILES.map(name => ({ path: 'cap-runtime/agentic_cap/'+name, sha256: 'a'.repeat(64) }));
  assert.equal(recoveryContract(files), recoveryContract([...files].reverse()));
  const prior = recoveryContract(files); files[0].sha256 = 'b'.repeat(64);
  assert.notEqual(recoveryContract(files), prior);
  assert.throws(() => recoveryContract(files.slice(1)));
  assert.equal(digest('value').length, 64);
});

async function decisionFixture(t, { age = .01, transport = 0 } = {}) {
  const store = await storeFixture(t), candidate = await store.propose(repairTrace());
  await store.validate(candidate.id, receipt(candidate));
  const q = query(); let imageRead = false, retrievedBeforeImage = false, modelContext;
  q.observation.captured_at -= 3600; // Deliberately different machine wall clocks.
  q.observation.age_seconds = age; q.observation.image_ref = 'frame-000120.png';
  const client = {
    request: async route => {
      if (route === '/health') return q.provenance;
      if (route.endsWith('/observe')) { await new Promise(r => setTimeout(r, transport)); return q.observation; }
      if (route.endsWith('/decisions')) return { event_id: 1 };
      throw new Error('Unexpected Runtime action: '+route);
    },
    bytes: async () => { imageRead = true; await new Promise(r => setTimeout(r, 30)); return Buffer.from('image'); }
  };
  const c = new RobotCoordinator({ root: store.root, configuration: async () => ({ active: {} }), client,
    model: async (_configuration, context) => { modelContext = context; return { decision: 'ask_user', message: 'unknown' }; },
    experience: { retrieve: async query => { retrievedBeforeImage = !imageRead; return store.retrieve(query); } } });
  c.state.episode = q.episode; c.task = 'cube_restack'; c.text = 'place'; c.images = [];
  c.taskStartedAt = Date.now(); c.abort = new AbortController();
  await c.acquireDecisionObservation(c.epoch);
  await c.decide(c.epoch, 'execution');
  return { context: modelContext, candidate, retrievedBeforeImage };
}

test('real store/coordinator integration retrieves across tasks before image download despite clock skew', async t => {
  const { context, candidate, retrievedBeforeImage } = await decisionFixture(t);
  assert.equal(retrievedBeforeImage, true);
  assert.equal(context.retrieved_experiences[0].id, candidate.id);
  assert.equal(context.retrieved_experiences[0].source_task, 'cube_stack');
  assert.equal(context.scenario, 'cube_restack');
  assert.ok(context.latest_observation_age_upper_seconds >= .04);
  assert.ok(context.latest_observation_age_upper_seconds < 1);
});

test('real store/coordinator integration counts network RTT and preserves the one-second evidence limit', async t => {
  const { context } = await decisionFixture(t, { age: .9, transport: 150 });
  assert.deepEqual(context.retrieved_experiences, []);
  assert.ok(context.latest_observation_age_upper_seconds > 1);
});
