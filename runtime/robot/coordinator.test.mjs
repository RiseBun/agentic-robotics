import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { RobotCoordinator } from './coordinator.mjs';
import { parseDecision, promptForContext, requestDecision } from './model.mjs';
import { RuntimeClient } from './runtime-client.mjs';

const observe = { decision: 'observe', message: '目标明确', goal: { holding: 'red cube' }, remaining_steps: [] };
const run = { decision: 'run_code', message: '执行', subgoal: '抬起', code: 'pick("red cube")', remaining_steps: [] };
const finish = { decision: 'finish', message: '请求验证', remaining_steps: [] };
function deferred() { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; }
async function until(fn) { const end = Date.now() + 4000; while (Date.now() < end) { if (fn()) return; await new Promise(r => setTimeout(r, 10)); } throw new Error('Timed out'); }
class Client {
  constructor() { this.calls = []; this.episode = null; this.effect = 'verified'; }
  async request(route, body) {
    this.calls.push({ route, body });
    if (route === '/episodes') return this.episode = { id: 'episode', revision: 1, status: 'ready', goal: body.goal, segments: {}, active_segment: null, used: { decisions: 0 } };
    if (route.endsWith('/cancel')) { if (this.episode) this.episode.active_segment = null; return { stopped: true }; }
    if (route.endsWith('/goal')) { this.episode.revision++; this.episode.goal = body.goal; return structuredClone(this.episode); }
    if (route.endsWith('/decisions')) { this.episode.used.decisions++; return { event_id: 1 }; }
    if (route.endsWith('/observe')) return { captured_at: Date.now()/1000, age_seconds: .02, joint_positions: [] };
    if (route.endsWith('/verify')) return { status: this.effect };
    if (route.endsWith('/segments')) {
      this.episode.segments.seg = { id: 'seg', execution: 'completed', effect: 'verified', code: body.code };
      return { segment_id: 'seg' };
    }
    if (route.endsWith('/close')) { this.episode.status = 'closed'; return structuredClone(this.episode); }
    return structuredClone(this.episode);
  }
  async events(_id, _after, signal) { await new Promise(resolve => { if (signal.aborted) resolve(); else signal.addEventListener('abort', resolve, { once: true }); }); }
  async bytes() { return Buffer.from('zip'); }
}
async function setup(model) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'robot-coordinator-'));
  const client = new Client();
  const c = new RobotCoordinator({ root, configuration: async () => ({ active: {} }), client, model });
  return { c, client, cleanup: async () => { c.eventsAbort?.abort(); c.abort?.abort(); await c.loopPromise; await c.recordQueue; await fs.rm(root, { recursive: true, force: true }); } };
}

test('goal -> short code -> runtime verification, complete records and budget', async () => {
  const decisions = [observe, run, finish];
  const { c, client, cleanup } = await setup(async () => decisions.shift());
  try {
    await c.start({ task: 'cube_lifting', text: '抬起红方块' });
    await c.loopPromise;
    assert.equal(c.state.status, 'completed');
    assert.equal(client.episode.used.decisions, 3);
    const pack = await c.export();
    assert.ok(pack.workbench_jsonl.includes('segment_feedback'));
    assert.equal(pack.episode_id, 'episode');
    assert.ok(pack.runtime_zip_base64);
  } finally { await cleanup(); }
});

function unknownScene() {
  return { captured_at: Date.now()/1000, age_seconds: .01, evidence_ref: `frame-${Date.now()}`,
    objects: { 'red cube': { status: 'unknown' } }, empty_gripper_evidence: { status: 'verified' } };
}

test('model history excludes raw event streams while retaining code, evidence and persisted audit',async()=>{
  const segment={id:'old',code:'check_holding("red cube")',execution:'failed',api_attempts:[],
    feedback:{category:'insufficient_evidence',evidence:{reason:'occluded'}},
    stage_verification:{status:'unknown',condition:{holding:'red cube'}},
    events:[{id:17,kind:'observation_ready',snapshot:'audit-only-'.repeat(20000)}]};
  let inspected=false;
  const fixture=await setup(async(_channel,context)=>{
    if(context.mode==='interpretation'){
      fixture.c.state.history.push(structuredClone(segment));return observe;
    }
    inspected=true;
    assert.equal(context.recent_attempts[0].events,undefined);
    assert.equal(context.recent_attempts[0].code,segment.code);
    assert.deepEqual(context.recent_attempts[0].feedback,segment.feedback);
    assert.deepEqual(context.recent_attempts[0].stage_verification,segment.stage_verification);
    assert.ok(JSON.stringify(context).length<10000);
    return finish;
  });
  try{
    await fixture.c.start({task:'cube_lifting',text:'抬起红方块'});await fixture.c.loopPromise;
    assert.equal(inspected,true);
    assert.deepEqual(fixture.c.state.history[0],segment);
    const checkpoint=JSON.parse(await fs.readFile(path.join(fixture.c.root,'current.json'),'utf8'));
    assert.deepEqual(checkpoint.state.history[0].events,segment.events);
  }finally{await fixture.cleanup();}
});

async function codeRepairFixture({ delayRepair = false } = {}) {
  let execution = 0, repairing = false, segments = 0;
  const late = deferred();
  const fixture = await setup(async (_channel, context) => {
    if (context.mode === 'interpretation') return observe;
    execution++;
    if (execution === 1) return { ...run, code: 'close_gripper()' };
    if (execution === 2) return { ...run, code: 's=get_execution_state()\np=s[0]' };
    assert.equal(context.recovery_progress.recommendation, 'revise_code');
    assert.equal(context.recovery_progress.consecutive_blocked_attempts, 1);
    assert.equal(context.recovery_progress.repeated_no_progress, false);
    assert.deepEqual(context.recovery_progress.program_repair.diagnostic.available_fields, ['tcp_xyz', 'tcp_wxyz']);
    repairing = true;
    return delayRepair ? late.promise : { ...run, code: 's=get_execution_state()\np=s["tcp_xyz"]' };
  });
  fixture.client.effect = 'failed';
  const original = fixture.client.request.bind(fixture.client);
  fixture.client.request = async (route, body) => {
    if (route.endsWith('/observe')) return unknownScene();
    if (!route.endsWith('/segments')) return original(route, body);
    fixture.client.calls.push({ route, body });
    const id = 'segment-'+(++segments);
    const segment = { id, code: body.code, execution: segments===2 ? 'failed' : 'completed',
      goal_verification: { status: segments===3 ? 'verified' : 'failed' },
      stage_verification: { status: 'unknown', reason: 'No public postcondition declared' } };
    if (segments===2) segment.feedback = { category: 'invalid_program', message: 'Dictionary has no valid key 0',
      evidence: { kind: 'invalid_subscript', line: 2, available_fields: ['tcp_xyz', 'tcp_wxyz'] } };
    fixture.client.episode.segments[id] = segment;
    if (segments===3) fixture.client.effect = 'verified';
    return { segment_id: id };
  };
  return Object.assign(fixture, { late, isRepairing: () => repairing, segmentCount: () => segments });
}

test('grasp -> field error -> corrected code uses one episode and the original decision ledger', async () => {
  const fixture = await codeRepairFixture();
  try {
    await fixture.c.start({ task: 'cube_lifting', text: '抬起红色方块' });
    await fixture.c.loopPromise;
    assert.equal(fixture.c.state.status, 'completed');
    assert.equal(fixture.client.calls.filter(c => c.route==='/episodes').length, 1);
    assert.equal(fixture.client.episode.used.decisions, 4);
    assert.equal(fixture.segmentCount(), 3);
    assert.equal(fixture.c.state.assistance, undefined);
  } finally { await fixture.cleanup(); }
});

test('stop during program repair fences an uncooperative late model result', async () => {
  const fixture = await codeRepairFixture({ delayRepair: true });
  try {
    await fixture.c.start({ task: 'cube_lifting', text: '抬起红色方块' });
    await until(fixture.isRepairing);
    await fixture.c.stop();
    fixture.late.resolve({ ...run, code: 's=get_execution_state()\np=s["tcp_xyz"]' });
    await fixture.c.loopPromise;
    assert.equal(fixture.c.state.status, 'stopped');
    assert.equal(fixture.segmentCount(), 2);
  } finally { fixture.late.resolve(run); await fixture.cleanup(); }
});

test('goal change during program repair keeps the episode and discards old repaired code', async () => {
  const fixture = await codeRepairFixture({ delayRepair: true });
  const originalModel = fixture.c.model;
  fixture.c.model = async (...args) => {
    const context = args[1];
    if (context.user_goal.includes('绿色')) return context.mode==='interpretation'
      ? { ...observe, goal: { holding: 'green cube' } } : finish;
    return originalModel(...args);
  };
  try {
    await fixture.c.start({ task: 'cube_lifting', text: '抬起红色方块' });
    await until(fixture.isRepairing);
    const oldLoop=fixture.c.loopPromise;
    fixture.client.effect='verified';
    await fixture.c.input({ text: '改为抬起绿色方块' });
    fixture.late.resolve({ ...run, code: 's=get_execution_state()\np=s["tcp_xyz"]' });
    await oldLoop; await fixture.c.loopPromise;
    assert.equal(fixture.c.state.status,'completed');
    assert.equal(fixture.c.state.episode.goal.holding,'green cube');
    assert.equal(fixture.segmentCount(),2);
    assert.equal(fixture.client.calls.filter(c=>c.route==='/episodes').length,1);
    assert.equal(fixture.client.episode.used.decisions,6);
  } finally { fixture.late.resolve(run); await fixture.cleanup(); }
});

test('model sees unchanged unknown evidence and autonomously asks for assistance with confirmed hold', async () => {
  let execution = 0;
  const fixture = await setup(async (_channel, context) => {
    if (context.mode === 'interpretation') return observe;
    execution++;
    if (execution === 3) {
      assert.equal(context.recovery_progress.repeated_no_progress, true);
      assert.equal(context.recovery_progress.consecutive_unchanged_attempts, 2);
      return { decision: 'ask_user', message: '红方块仍不可见，请恢复相机观测。', remaining_steps: [] };
    }
    return { decision: 'observe', message: '获取新观测', remaining_steps: [] };
  });
  const original = fixture.client.request.bind(fixture.client);
  fixture.client.request = (route, ...args) => route.endsWith('/observe') ? unknownScene() : original(route, ...args);
  try {
    await fixture.c.start({ task: 'cube_lifting', text: '抬起红方块' }); await fixture.c.loopPromise;
    assert.equal(fixture.c.state.status, 'awaiting_user');
    assert.equal(fixture.c.state.assistance.origin, 'model');
    assert.equal(fixture.c.state.assistance.hold_confirmed, true);
    assert.equal(fixture.client.calls.filter(c => c.route.endsWith('/segments')).length, 0);
    assert.equal(fixture.client.episode.used.decisions, 4);
  } finally { await fixture.cleanup(); }
});

test('system guard identifies a noncompliant repeated observation separately from autonomous ask_user', async () => {
  const fixture = await setup(async (_channel, context) => context.mode === 'interpretation' ? observe
    : { decision: 'observe', message: '再次观察', remaining_steps: [] });
  const original = fixture.client.request.bind(fixture.client);
  fixture.client.request = (route, ...args) => route.endsWith('/observe') ? unknownScene() : original(route, ...args);
  try {
    await fixture.c.start({ task: 'cube_lifting', text: '抬起红方块' }); await fixture.c.loopPromise;
    assert.equal(fixture.c.state.assistance.origin, 'recovery_guard');
    assert.equal(fixture.c.state.assistance.block.code, 'REPEATED_NO_EVIDENCE_PROGRESS');
    assert.equal(fixture.client.episode.used.decisions, 4);
  } finally { await fixture.cleanup(); }
});

test('an unchanged invalid program is dispatched once even with delayed SSE, then held for assistance', async () => {
  const fixture = await setup(async (_channel, context) => context.mode === 'interpretation' ? observe : run);
  const original = fixture.client.request.bind(fixture.client);
  fixture.client.request = async (route, ...args) => {
    const result = await original(route, ...args);
    if (route.endsWith('/segments')) Object.assign(fixture.client.episode.segments.seg, {
      execution: 'failed', api_attempts: [], feedback: { category: 'invalid_program', message: 'Unknown call pick' } });
    return result;
  };
  try {
    await fixture.c.start({ task: 'cube_lifting', text: '抬起红方块' }); await fixture.c.loopPromise;
    assert.equal(fixture.client.calls.filter(c => c.route.endsWith('/segments')).length, 1);
    assert.equal(fixture.c.state.assistance.origin, 'recovery_guard');
    assert.equal(fixture.c.state.recovery.attempts[0].api_attempts.length, 0);
  } finally { await fixture.cleanup(); }
});

test('stop while assistance hold waits discards the late assistance status', async () => {
  let holding = false;
  const late = deferred();
  const fixture = await setup(async (_channel, context) => context.mode === 'interpretation' ? observe
    : { decision: 'ask_user', message: '需要观测', remaining_steps: [] });
  const original = fixture.client.request.bind(fixture.client);
  fixture.client.request = (route, ...args) => {
    if (route.endsWith('/cancel') && !holding) { holding = true; return late.promise; }
    return original(route, ...args);
  };
  try {
    await fixture.c.start({ task: 'cube_lifting', text: '抬起红方块' }); await until(() => holding);
    const stop = fixture.c.stop(); late.resolve({ stopped: true });
    await stop; await fixture.c.loopPromise;
    assert.equal(fixture.c.state.status, 'stopped');
    assert.equal(fixture.c.state.assistance, undefined);
  } finally { late.resolve({ stopped: true }); await fixture.cleanup(); }
});

test('goal change while assistance hold waits rejects the old response and preserves cumulative budget', async () => {
  let holding = false;
  const late = deferred();
  const fixture = await setup(async (_channel, context) => context.mode === 'interpretation'
    ? { ...observe, goal: { holding: context.user_goal.includes('绿色') ? 'green cube' : 'red cube' } }
    : { decision: 'ask_user', message: context.user_goal.includes('绿色') ? '需要绿色方块证据' : '需要红色方块证据', remaining_steps: [] });
  const original = fixture.client.request.bind(fixture.client);
  fixture.client.request = (route, ...args) => {
    if (route.endsWith('/cancel') && !holding) { holding = true; return late.promise; }
    return original(route, ...args);
  };
  try {
    await fixture.c.start({ task: 'cube_lifting', text: '抬起红色方块' }); await until(() => holding);
    const old = fixture.c.loopPromise;
    const changed = fixture.c.input({ text: '改为抬起绿色方块' });
    late.resolve({ stopped: true });
    await changed; await old; await fixture.c.loopPromise;
    assert.deepEqual(fixture.c.state.episode.goal, { holding: 'green cube' });
    assert.equal(fixture.c.state.assistance.message, '需要绿色方块证据');
    assert.equal(fixture.c.state.status, 'awaiting_user');
    assert.equal(fixture.client.episode.used.decisions, 4);
    assert.equal(fixture.client.calls.filter(c => c.route==='/episodes').length, 1);
    assert.equal(fixture.client.calls.filter(c => c.route.endsWith('/segments')).length, 0);
  } finally { late.resolve({ stopped: true }); await fixture.cleanup(); }
});

test('resuming an awaiting-user task clears its old assistance before the new model responds', async () => {
  const late = deferred(); let interpretations = 0;
  const fixture = await setup(async (_channel, context) => {
    if (context.mode === 'interpretation') return ++interpretations === 1 ? observe : late.promise;
    return { decision: 'ask_user', message: '恢复红方块观测', remaining_steps: [] };
  });
  try {
    await fixture.c.start({ task: 'cube_lifting', text: '抬起红方块' }); await fixture.c.loopPromise;
    assert.equal(fixture.c.state.assistance.hold_confirmed, true);
    await fixture.c.input({ text: '改成绿方块' }); await until(() => interpretations===2);
    assert.equal(fixture.c.state.assistance, undefined);
    assert.equal(fixture.c.state.stop_confirmed, undefined);
    await fixture.c.stop(); late.resolve({ ...observe, goal: { holding: 'green cube' } }); await fixture.c.loopPromise;
    assert.equal(fixture.c.state.status, 'stopped');
  } finally { late.resolve(observe); await fixture.cleanup(); }
});

test('execution retrieves historical hints and audits corpus without exposing validation receipts', async () => {
  const contexts = [], queries = [];
  const fixture = await setup(async (_model, context) => {
    contexts.push(context); return context.mode === 'interpretation' ? observe : finish;
  });
  const { c } = fixture;
  c.experience = { retrieve: async q => {
    queries.push(q);
    return { matches: [{ id: 'case', actions: [{ decision: 'observe' }] }], rejected: [], corpus_sha256: 'corpus', usage: 'Rebind and reverify' };
  }, propose: async trace => { assert.equal(trace.protocol, 1); return { id: 'new-candidate' }; } };
  const original = fixture.client.request.bind(fixture.client);
  fixture.client.request = async (route, ...args) => route.endsWith('/trace')
    ? { protocol: 1 } : route === '/health' ? { backend: 'cap', recovery_contract: 'contract' } : original(route, ...args);
  try {
    await c.start({ task: 'cube_lifting', text: '抬起红方块' }); await c.loopPromise;
    assert.equal(contexts[0].retrieved_experiences, undefined);
    assert.equal(contexts[1].retrieved_experiences[0].id, 'case');
    assert.equal(queries[0].episode.id, 'episode');
    assert.equal(queries[0].provenance.recovery_contract, 'contract');
    assert.equal(queries[0].observation.captured_at, contexts[1].latest_observation.captured_at);
    assert.ok(queries[0].observation_age_upper_seconds >= .02 && queries[0].observation_age_upper_seconds < 1);
    await c.close();
    const pack = await c.export();
    const rows = pack.workbench_jsonl.trim().split('\n').map(JSON.parse);
    assert.equal(rows.find(r => r.kind === 'experience_retrieval').corpus_sha256, 'corpus');
    assert.equal(rows.find(r => r.kind === 'experience_candidate').validated, false);
  } finally { await fixture.cleanup(); }
});

test('only a coherent registered wrist frame is attached as the second scene image', async () => {
  for (const mode of ['valid','old_step','old_episode','unrecorded_file']) {
    let sentImages;
    const fixture=await setup(async (_model,context,images) => {
      if(context.mode==='interpretation')return observe;
      sentImages=images;return finish;
    });
    const original=fixture.client.request.bind(fixture.client);
    fixture.client.request=async(route,...args)=>{
      const result=await original(route,...args);
      if(!route.endsWith('/observe'))return result;
      const wrist={camera_name:'robot0_eye_in_hand',episode_id:'episode',control_step:120,
        captured_at:result.captured_at,image_ref:'view-robot0_eye_in_hand-000120.png'};
      if(mode==='old_step')wrist.control_step--;
      if(mode==='old_episode')wrist.episode_id='other';
      if(mode==='unrecorded_file')wrist.image_ref='../../secret.png';
      return {...result,image_ref:'frame-000120.png',control_step:120,camera_views:{robot0_eye_in_hand:wrist}};
    };
    try{
      await fixture.c.start({task:'cube_lifting',text:'抬起红方块'});await fixture.c.loopPromise;
      assert.equal(sentImages.length,mode==='valid'?2:1);
    }finally{await fixture.cleanup();}
  }
});

test('stop while retrieving experience rejects a late hint and never calls the execution model', async () => {
  const late = deferred(); let retrieving = false, executionModels = 0;
  const fixture = await setup(async (_model, context) => {
    if (context.mode !== 'interpretation') executionModels++;
    return observe;
  });
  fixture.c.experience = { retrieve: async () => { retrieving = true; return late.promise; } };
  try {
    await fixture.c.start({ task: 'cube_lifting', text: '抬起红方块' });
    await until(() => retrieving);
    await fixture.c.stop(); await fixture.c.loopPromise;
    late.resolve({ matches: [{ id: 'late' }], rejected: [] });
    await new Promise(done => setTimeout(done, 10));
    assert.equal(executionModels, 0);
    assert.equal(fixture.c.state.status, 'stopped');
    assert.equal(fixture.client.calls.filter(x => x.route.endsWith('/segments')).length, 0);
  } finally { late.resolve({ matches: [], rejected: [] }); await fixture.cleanup(); }
});

test('unavailable experience falls back to the existing model loop without changing Runtime verification', async () => {
  const fixture = await setup(async (_model, context) => context.mode === 'interpretation' ? observe : finish);
  fixture.c.experience = { retrieve: async () => { throw new Error('corrupt corpus'); } };
  try {
    await fixture.c.start({ task: 'cube_lifting', text: '抬起红方块' }); await fixture.c.loopPromise;
    assert.equal(fixture.c.state.status, 'completed');
    const pack = await fixture.c.export();
    assert.ok(pack.workbench_jsonl.includes('experience_unavailable'));
    assert.equal(fixture.client.calls.filter(x => x.route.endsWith('/verify')).length, 1);
  } finally { await fixture.cleanup(); }
});

test('goal change while retrieval waits discards the old hints and decision', async () => {
  const late = deferred(); let retrieving = false, count = 0;
  const contexts = [];
  const fixture = await setup(async (_model, context) => {
    contexts.push(context);
    return context.mode === 'interpretation'
      ? { ...observe, goal: { holding: context.user_goal.includes('绿色') ? 'green cube' : 'red cube' } } : finish;
  });
  fixture.c.experience = { retrieve: async () => {
    if (++count === 1) { retrieving = true; return late.promise; }
    return { matches: [], rejected: [], corpus_sha256: 'corpus' };
  } };
  try {
    await fixture.c.start({ task: 'cube_lifting', text: '抬起红色方块' });
    await until(() => retrieving);
    const oldLoop = fixture.c.loopPromise;
    await fixture.c.input({ text: '改为抬起绿色方块' });
    await oldLoop; late.resolve({ matches: [{ id: 'obsolete' }], rejected: [] });
    await fixture.c.loopPromise;
    const executions = contexts.filter(c => c.mode === 'execution');
    assert.equal(executions.length, 1);
    assert.equal(executions[0].goal.holding, 'green cube');
    assert.deepEqual(executions[0].retrieved_experiences, []);
    assert.equal(fixture.client.calls.filter(c => c.route === '/episodes').length, 1);
  } finally { late.resolve({ matches: [], rejected: [] }); await fixture.cleanup(); }
});

test('uncooperative experience retrieval times out and does not hang the model loop', { timeout: 5000 }, async () => {
  const fixture = await setup(async (_model, context) => context.mode === 'interpretation' ? observe : finish);
  fixture.c.experience = { retrieve: () => new Promise(() => {}) };
  try {
    await fixture.c.start({ task: 'cube_lifting', text: '抬起红方块' }); await fixture.c.loopPromise;
    assert.equal(fixture.c.state.status, 'completed');
    assert.ok((await fixture.c.export()).workbench_jsonl.includes('Experience retrieval timed out'));
  } finally { await fixture.cleanup(); }
});

test('prepared scene continues through the execution loop without replacing its goal or budget', async () => {
  let calls = 0;
  const fixture = await setup(async (_channel, context) => {
    assert.equal(context.mode, 'execution');
    assert.equal(context.goal.holding, 'red cube');
    return ++calls === 1 ? run : finish;
  });
  const { c, client } = fixture;
  try {
    client.episode = { id: 'existing', revision: 7, status: 'ready', goal: { holding: 'red cube' },
      segments: {}, active_segment: null, used: { decisions: 6 } };
    c.state.episode = structuredClone(client.episode);
    c.taskId = 'prepared'; c.task = 'cube_lifting'; c.text = '抬起红方块'; c.images = [];
    c.taskStartedAt = Date.now()-25_000; c.abort = new AbortController();
    c.launchLoop(c.epoch, () => c.runExecution(c.epoch));
    await c.loopPromise;
    assert.equal(c.state.status, 'completed');
    assert.equal(c.state.episode.id, 'existing');
    assert.equal(c.state.episode.revision, 7);
    assert.equal(client.episode.used.decisions, 8);
    assert.equal(client.calls.filter(x => x.route === '/episodes' || x.route.endsWith('/goal')).length, 0);
  } finally { await fixture.cleanup(); }
});

test('Runtime expiration on reconnect fences a hanging model and updates the task state', async () => {
  const late = deferred();
  let thinking = false;
  const fixture = await setup(async () => { thinking = true; return late.promise; });
  const { c, client } = fixture;
  try {
    client.episode = { id: 'existing', revision: 7, status: 'ready', goal: { holding: 'red cube' },
      segments: {}, active_segment: null, used: { decisions: 6 } };
    c.state.episode = structuredClone(client.episode);
    c.taskId = 'prepared'; c.task = 'cube_lifting'; c.text = '抬起红方块'; c.images = [];
    c.taskStartedAt = Date.now(); c.abort = new AbortController();
    c.launchLoop(c.epoch, () => c.runExecution(c.epoch));
    await until(() => thinking);
    client.episode.status = 'closed';
    client.episode.termination_reason = 'wall_budget_exhausted';
    await c.refresh(); await c.loopPromise;
    assert.equal(c.state.status, 'needs_attention');
    assert.match(c.state.messages.at(-1).text, /Runtime 已确认保持/);
    late.resolve(run);
    await new Promise(done => setTimeout(done, 10));
    assert.equal(client.calls.filter(x => x.route.endsWith('/segments')).length, 0);
  } finally { late.resolve(run); await fixture.cleanup(); }
});
test('stop during model thinking rejects late reply even if provider ignores abort', { timeout: 1500 }, async () => {
  const late = deferred(); let calls = 0;
  const { c, client, cleanup } = await setup(async () => ++calls === 1 ? observe : late.promise);
  try {
    await c.start({ task: 'cube_lifting', text: '抬起红方块' });
    await until(() => calls === 2);
    await c.stop(); await c.loopPromise; late.resolve(run);
    assert.equal(c.state.status, 'stopped');
    assert.equal(client.calls.filter(x => x.route.endsWith('/segments')).length, 0);
  } finally { late.resolve(run); await cleanup(); }
});

test('independent holding alert abandons a hanging model and replans without replaying late code', { timeout: 2000 }, async () => {
  const late = deferred();
  let calls = 0, receive;
  const fixture = await setup(async () => ++calls === 1 ? observe : calls === 2 ? late.promise : finish);
  const { c, client } = fixture;
  client.events = async (_id, _cursor, signal, callback) => {
    receive = callback;
    await new Promise(resolve => signal.addEventListener('abort', resolve, { once: true }));
  };
  try {
    await c.start({ task: 'cube_lifting', text: '抬起红色方块' });
    await until(() => calls === 2 && receive && c.pendingModel);
    client.episode.revision = 2;
    client.episode.runtime_alert = { code: 'PUBLIC_HOLDING_LOSS' };
    await receive({ id: 1, episode_id: 'episode', kind: 'public_holding_contradicted', revision: 2 });
    await c.loopPromise;
    assert.equal(calls, 3);
    assert.equal(c.state.status, 'completed');
    late.resolve(run);
    await new Promise(resolve => setTimeout(resolve, 20));
    assert.equal(client.calls.filter(x => x.route.endsWith('/segments')).length, 0);
    assert.ok(c.state.events.some(e => e.kind === 'public_holding_contradicted'));
    assert.equal(client.episode.used.decisions, 3);
  } finally { late.resolve(run); await fixture.cleanup(); }
});

test('pending protective hold waits for Runtime without spending another model decision', async () => {
  let models = 0, pending = true;
  const fixture = await setup(async () => ++models === 1 ? observe : finish);
  const original = fixture.client.request.bind(fixture.client);
  fixture.client.request = async (route, ...args) => {
    if (route.endsWith('/decisions') && !args[0].request_id.startsWith('interpretation-') && pending) {
      pending = false;
      throw Object.assign(new Error('Protective hold pending'), { code: 'HOLD_PENDING' });
    }
    return original(route, ...args);
  };
  try {
    await fixture.c.start({ task: 'cube_lifting', text: '抬起红色方块' });
    await fixture.c.loopPromise;
    assert.equal(fixture.c.state.status, 'completed');
    assert.equal(models, 2);
    assert.equal(fixture.client.episode.used.decisions, 2);
  } finally { await fixture.cleanup(); }
});

test('stop during model configuration loading prevents late model dispatch and status changes', { timeout: 1500 }, async () => {
  const late = deferred(); let loading = false, calls = 0;
  const fixture = await setup(async () => { calls++; return observe; });
  fixture.c.configuration = async () => { loading = true; return late.promise; };
  try {
    await fixture.c.start({ task: 'cube_lifting', text: '抬起红方块' });
    await until(() => loading);
    await fixture.c.stop(); await fixture.c.loopPromise; late.resolve({ active: {} });
    assert.equal(fixture.c.state.status, 'stopped');
    assert.equal(calls, 0);
    assert.equal(fixture.client.calls.filter(x => x.route === '/episodes').length, 0);
  } finally { late.resolve({ active: {} }); await fixture.cleanup(); }
});
test('changing goal invalidates old reasoning and keeps one scene', async () => {
  const late = deferred(); let calls = 0;
  const { c, client, cleanup } = await setup(async () => { calls++; return calls === 1 ? observe : calls === 2 ? late.promise : calls === 3 ? { ...observe, goal: { holding: 'green cube' } } : finish; });
  try {
    await c.start({ task: 'cube_lifting', text: '抬起红方块' });
    await until(() => calls === 2);
    const old = c.loopPromise;
    await c.input({ text: '改成绿方块' }); late.resolve(run);
    await old; await c.loopPromise;
    assert.equal(client.calls.filter(x => x.route === '/episodes').length, 1);
    assert.equal(client.calls.filter(x => x.route.endsWith('/segments')).length, 0);
    assert.equal(c.state.episode.revision, 2);
  } finally { late.resolve(run); await cleanup(); }
});

test('goal change refreshes a rejected decision reservation without losing the new intent or charging twice', async () => {
  const late = deferred(); let calls = 0;
  const fixture = await setup(async () => {
    calls++;
    return calls === 1 ? observe : calls === 2 ? late.promise
      : calls === 3 ? { ...observe, goal: { holding: 'green cube' } }
      : { decision: 'ask_user', message: '新目标已登记', remaining_steps: [] };
  });
  const original = fixture.client.request.bind(fixture.client);
  const reservations = [];
  let rejectNext = false;
  fixture.client.request = async (route, body) => {
    if (route.endsWith('/decisions') && rejectNext) {
      reservations.push(body);
      if (reservations.length === 1) {
        fixture.client.episode.revision++;
        const error = new Error('Stale scene revision'); error.code = 'STALE_REVISION'; throw error;
      }
    }
    return original(route, body);
  };
  try {
    await fixture.c.start({ task: 'cube_lifting', text: '抬起红方块' });
    await until(() => calls === 2);
    const old = fixture.c.loopPromise;
    rejectNext = true;
    await fixture.c.input({ text: '改成绿方块' }); late.resolve(run);
    await old; await fixture.c.loopPromise;
    assert.equal(fixture.c.state.status, 'awaiting_user');
    assert.deepEqual(fixture.client.episode.goal, { holding: 'green cube' });
    assert.equal(reservations[0].request_id, reservations[1].request_id);
    assert.equal(fixture.client.episode.used.decisions, calls);
    assert.equal(fixture.client.calls.filter(x => x.route === '/episodes').length, 1);
    assert.equal(fixture.client.calls.filter(x => x.route.endsWith('/segments')).length, 0);
  } finally { late.resolve(run); await fixture.cleanup(); }
});

test('stop during a stale decision reservation prevents model requests and reservation retries', async () => {
  let calls = 0, refreshing = false, rejects = 0;
  const late = deferred();
  const fixture = await setup(async () => ++calls === 1 ? observe : run);
  const original = fixture.client.request.bind(fixture.client);
  fixture.client.request = async (route, body) => {
    if (route.endsWith('/decisions') && !body.request_id.startsWith('interpretation-')) {
      rejects++; refreshing = true;
      const error = new Error('Stale scene revision'); error.code = 'STALE_REVISION'; throw error;
    }
    if (route === '/episodes/episode' && refreshing) return late.promise;
    return original(route, body);
  };
  try {
    await fixture.c.start({ task: 'cube_lifting', text: '抬起红方块' });
    await until(() => refreshing);
    const stopping = fixture.c.stop(); late.resolve(structuredClone(fixture.client.episode));
    await stopping; await fixture.c.loopPromise;
    assert.equal(fixture.c.state.status, 'stopped');
    assert.equal(calls, 1);
    assert.equal(rejects, 1);
    assert.equal(fixture.client.calls.filter(x => x.route.endsWith('/segments')).length, 0);
  } finally { late.resolve(structuredClone(fixture.client.episode)); await fixture.cleanup(); }
});
test('unknown cannot become completion', async () => {
  let calls = 0;
  const { c, client, cleanup } = await setup(async () => ++calls === 1 ? observe : calls === 2 ? finish : { decision: 'ask_user', message: '需要更清晰观测', remaining_steps: [] });
  client.effect = 'unknown';
  try {
    await c.start({ task: 'cube_lifting', text: '抬起' }); await c.loopPromise;
    assert.equal(c.state.status, 'awaiting_user');
    assert.equal(c.state.verification.status, 'unknown');
  } finally { await cleanup(); }
});

test('all legacy tool decisions rejected', () => {
  for (const decision of ['ssh', 'terminal', 'browser', 'write_file', 'spawn_agent']) assert.throws(() => parseDecision(JSON.stringify({ decision })));
});

test('stage contract reaches runtime and final verification remains authoritative', async () => {
  const contract = { postcondition: { holding: 'red cube' } };
  const decisions = [observe, { ...run, contract }, finish];
  const contexts = [];
  const fixture = await setup(async (_channel, context) => { contexts.push(context); return decisions.shift(); });
  const original = fixture.client.request.bind(fixture.client);
  fixture.client.request = async (route, body) => {
    const result = await original(route, body);
    if (route.endsWith('/segments')) Object.assign(fixture.client.episode.segments.seg, {
      stage_verification: { status: 'verified' }, goal_verification: { status: 'failed', evidence_ref: 'public-frame' }
    });
    return result;
  };
  try {
    await fixture.c.start({ task: 'cube_lifting', text: '抬起' }); await fixture.c.loopPromise;
    assert.deepEqual(fixture.client.calls.find(x => x.route.endsWith('/segments')).body.contract, contract);
    assert.equal(contexts.at(-1).recent_attempts[0].stage_verification.status, 'verified');
    assert.equal(contexts.at(-1).recent_attempts[0].goal_verification.status, 'failed');
    assert.equal(fixture.c.state.status, 'completed'); // Only the subsequent finish response verifies it.
  } finally { await fixture.cleanup(); }
});

test('Runtime final-goal evidence finishes at the last model decision without another model call', async () => {
  let calls = 0;
  const fixture = await setup(async () => {
    if (++calls > 2) throw new Error('No model decision remains');
    return calls === 1 ? observe : run;
  });
  const original = fixture.client.request.bind(fixture.client);
  fixture.client.request = async (route, body) => {
    const result = await original(route, body);
    if (route.endsWith('/segments')) fixture.client.episode.segments.seg.goal_verification = { status: 'verified' };
    return result;
  };
  try {
    await fixture.c.start({ task: 'cube_lifting', text: '抬起红方块' }); await fixture.c.loopPromise;
    assert.equal(calls, 2);
    assert.equal(fixture.client.episode.used.decisions, 2);
    assert.equal(fixture.c.state.status, 'completed');
    assert.equal(fixture.client.calls.filter(x => x.route.endsWith('/verify')).length, 1);
    assert.equal(fixture.c.state.history.at(-1).source, 'runtime_segment_goal');
    const saved = JSON.parse(await fs.readFile(path.join(fixture.c.root, 'current.json'), 'utf8'));
    assert.equal(saved.state.status, 'completed');
  } finally { await fixture.cleanup(); }
});

test('automatic completion rechecks fresh evidence; old verified cannot override unknown', async () => {
  let calls = 0;
  const fixture = await setup(async () => ++calls === 1 ? observe : calls === 2 ? run
    : { decision: 'ask_user', message: '最新证据不足', remaining_steps: [] });
  const original = fixture.client.request.bind(fixture.client);
  fixture.client.request = async (route, body) => {
    const result = await original(route, body);
    if (route.endsWith('/segments')) fixture.client.episode.segments.seg.goal_verification = { status: 'verified' };
    return result;
  };
  fixture.client.effect = 'unknown';
  try {
    await fixture.c.start({ task: 'cube_lifting', text: '抬起红方块' }); await fixture.c.loopPromise;
    assert.equal(fixture.c.state.status, 'awaiting_user');
    assert.equal(fixture.c.state.verification.status, 'unknown');
  } finally { await fixture.cleanup(); }
});

test('scene revision at completion refreshes verification without another model decision or action', async () => {
  let calls = 0, checks = 0;
  const fixture = await setup(async () => {
    if (++calls > 2) throw new Error('No model decision remains');
    return calls === 1 ? observe : run;
  });
  const original = fixture.client.request.bind(fixture.client);
  fixture.client.request = async (route, body) => {
    if (route.endsWith('/verify') && ++checks === 1) {
      fixture.client.episode.revision++;
      const error = new Error('Stale scene revision'); error.code = 'STALE_REVISION'; throw error;
    }
    const result = await original(route, body);
    if (route.endsWith('/segments')) fixture.client.episode.segments.seg.goal_verification = { status: 'verified' };
    return result;
  };
  try {
    await fixture.c.start({ task: 'cube_lifting', text: '抬起红方块' }); await fixture.c.loopPromise;
    assert.equal(fixture.c.state.status, 'completed');
    assert.equal(calls, 2);
    assert.equal(checks, 2);
    assert.equal(fixture.client.episode.used.decisions, 2);
    assert.equal(fixture.client.calls.filter(x => x.route.endsWith('/segments')).length, 1);
    assert.equal(fixture.client.calls.find(x => x.route.endsWith('/verify')).body.revision, 2);
  } finally { await fixture.cleanup(); }
});

test('refreshed verification remains unknown and repeated revision changes have a bounded retry', async () => {
  for (const keepChanging of [false, true]) {
    let calls = 0, checks = 0;
    const fixture = await setup(async () => ++calls === 1 ? observe : calls === 2 ? run
      : { decision: 'ask_user', message: '证据不足', remaining_steps: [] });
    const original = fixture.client.request.bind(fixture.client);
    fixture.client.effect = 'unknown';
    fixture.client.request = async (route, body) => {
      if (route.endsWith('/verify') && (++checks === 1 || keepChanging)) {
        fixture.client.episode.revision++;
        const error = new Error('Stale scene revision'); error.code = 'STALE_REVISION'; throw error;
      }
      const result = await original(route, body);
      if (route.endsWith('/segments')) fixture.client.episode.segments.seg.goal_verification = { status: 'verified' };
      return result;
    };
    try {
      await fixture.c.start({ task: 'cube_lifting', text: '抬起红方块' }); await fixture.c.loopPromise;
      assert.equal(fixture.c.state.status, 'awaiting_user');
      assert.equal(fixture.c.state.verification.status, 'unknown');
      assert.equal(checks, 2);
      assert.equal(fixture.client.calls.filter(x => x.route.endsWith('/segments')).length, 1);
    } finally { await fixture.cleanup(); }
  }
});

test('goal change discovered while refreshing cannot confirm the old completion', async () => {
  let calls = 0, checks = 0;
  const fixture = await setup(async () => ++calls === 1 ? observe : run);
  const original = fixture.client.request.bind(fixture.client);
  fixture.client.request = async (route, body) => {
    if (route.endsWith('/verify')) {
      checks++;
      fixture.client.episode.goal = { holding: 'green cube' };
      fixture.client.episode.revision++;
      const error = new Error('Stale goal revision'); error.code = 'STALE_REVISION'; throw error;
    }
    const result = await original(route, body);
    if (route.endsWith('/segments')) fixture.client.episode.segments.seg.goal_verification = { status: 'verified' };
    return result;
  };
  try {
    await fixture.c.start({ task: 'cube_lifting', text: '抬起红方块' }); await fixture.c.loopPromise;
    assert.equal(fixture.c.state.status, 'needs_attention');
    assert.equal(checks, 1);
    assert.equal(calls, 2);
    assert.ok(!fixture.c.state.history.some(h => h.finish_request));
  } finally { await fixture.cleanup(); }
});

test('stop during revision refresh discards the pending completion and prevents a verification retry', async () => {
  let calls = 0, checks = 0, refreshing = false;
  const late = deferred();
  const fixture = await setup(async () => ++calls === 1 ? observe : run);
  const original = fixture.client.request.bind(fixture.client);
  fixture.client.request = async (route, body) => {
    if (route.endsWith('/verify')) {
      checks++; refreshing = true;
      const error = new Error('Stale scene revision'); error.code = 'STALE_REVISION'; throw error;
    }
    if (route === '/episodes/episode' && refreshing) return late.promise;
    const result = await original(route, body);
    if (route.endsWith('/segments')) fixture.client.episode.segments.seg.goal_verification = { status: 'verified' };
    return result;
  };
  try {
    await fixture.c.start({ task: 'cube_lifting', text: '抬起红方块' });
    await until(() => refreshing);
    const stopping = fixture.c.stop();
    late.resolve(structuredClone(fixture.client.episode));
    await stopping; await fixture.c.loopPromise;
    assert.equal(fixture.c.state.status, 'stopped');
    assert.equal(checks, 1);
    assert.ok(!fixture.c.state.history.some(h => h.finish_request));
  } finally { late.resolve(structuredClone(fixture.client.episode)); await fixture.cleanup(); }
});

test('stop during automatic verification rejects a late success response', async () => {
  const late = deferred(); let calls = 0, verifying = false;
  const fixture = await setup(async () => ++calls === 1 ? observe : run);
  const original = fixture.client.request.bind(fixture.client);
  fixture.client.request = async (route, body) => {
    if (route.endsWith('/verify')) { verifying = true; return late.promise; }
    const result = await original(route, body);
    if (route.endsWith('/segments')) fixture.client.episode.segments.seg.goal_verification = { status: 'verified' };
    return result;
  };
  try {
    await fixture.c.start({ task: 'cube_lifting', text: '抬起红方块' });
    await until(() => verifying);
    await fixture.c.stop(); late.resolve({ status: 'verified' }); await fixture.c.loopPromise;
    assert.equal(fixture.c.state.status, 'stopped');
  } finally { late.resolve({ status: 'verified' }); await fixture.cleanup(); }
});

test('contracts cannot introduce arbitrary verifiers or truth access', () => {
  for (const contract of [{ arbitrary_verifier: 'reward' }, { postcondition: { reward: true } }, { preconditions: 'truth' }, { postcondition: {} }]) {
    assert.throws(() => parseDecision(JSON.stringify({ ...run, contract })));
  }
});

test('offline restart shows unconfirmed hold and does not replay code', async () => {
  const { c, client, cleanup } = await setup(async () => { throw new Error('Must not invoke model'); });
  try {
    await fs.writeFile(path.join(c.root, 'current.json'), JSON.stringify({ taskId: 'saved', task: 'cube_lifting',
      state: { ...c.state, episode: { id: 'saved-episode', status: 'ready' } } }));
    let suppliedSignal;
    client.request = async (_route, _body, signal) => { suppliedSignal = signal; throw new Error('offline'); };
    await c.restore();
    assert.ok(suppliedSignal instanceof AbortSignal);
    assert.equal(c.state.status, 'needs_attention');
    assert.equal(c.state.connection_error, 'offline');
    assert.equal(c.state.episode.id, 'saved-episode');
    assert.equal(c.loopPromise, undefined);
  } finally { await cleanup(); }
});
test('material scene change invalidates decision revision before dispatch', async () => {
  let count = 0, fixture;
  const setupResult = await setup(async () => {
    count++;
    if (count === 1) return observe;
    if (count === 2) { fixture.client.episode.revision++; return run; }
    return finish;
  });
  fixture = setupResult;
  try {
    await fixture.c.start({ task: 'cube_lifting', text: '抬起红方块' }); await fixture.c.loopPromise;
    assert.equal(fixture.client.calls.filter(x => x.route.endsWith('/segments')).length, 0);
    assert.equal(fixture.c.state.status, 'completed');
  } finally { await fixture.cleanup(); }
});
test('provider sees images and JSON schema prompt; request records omit authorization', async () => {
  const records = [];
  await requestDecision({ api: 'openai-completions', baseURL: 'https://example.test/v1', model: 'vision', apiKey: 'secret-key' }, { user_goal: 'x' }, ['data:image/png;base64,YQ=='], new AbortController().signal,
    async (kind, value) => records.push({ kind, value }), async (_url, options) => {
      const body = JSON.parse(options.body);
      assert.ok(body.messages[1].content.some(x => x.type === 'image_url'));
      assert.equal(options.headers.Authorization, 'Bearer secret-key');
      return { ok: true, json: async () => ({ choices: [{ message: { content: JSON.stringify(observe) } }] }) };
    });
  assert.ok(!JSON.stringify(records).includes('secret-key'));
});
test('candidate-only active evidence instructions follow Runtime capability', () => {
  const candidate = promptForContext({ latest_observation: { capabilities: ['inspect_support'] } });
  const baseline = promptForContext({ latest_observation: { capabilities: [] } });
  assert.match(candidate, /inspect_support/);
  assert.doesNotMatch(baseline, /inspect_support/);
  assert.doesNotMatch(candidate, /inspect_placement/);
  assert.doesNotMatch(baseline, /inspect_placement/);
  const placement = promptForContext({ latest_observation: { capabilities: ['inspect_placement'] } });
  const both = promptForContext({ latest_observation: { capabilities: ['inspect_support', 'inspect_placement'] } });
  assert.match(placement, /inspect_placement/);
  assert.doesNotMatch(placement, /inspect_support/);
  assert.match(both, /inspect_support/);
  assert.match(both, /inspect_placement/);
  assert.match(placement, /Never use this skill to bypass a rejected release/);
});
test('final segment evidence survives delayed SSE start feedback', async () => {
  let calls = 0;
  const finalEvidence = { status: 'available', evidence_ref: 'frame-new', views_used: 1, captured_at: Date.now()/1000 };
  const fixture = await setup(async (_channel, context) => {
    if (++calls === 1) return observe;
    if (calls === 2) return run;
    assert.equal(context.latest_evidence.status, 'available');
    assert.equal(context.latest_evidence.evidence_ref, 'frame-new');
    assert.match(context.latest_evidence.usage, /Historical/);
    return finish;
  });
  const original = fixture.client.request.bind(fixture.client);
  fixture.client.request = async (route, body) => {
    const result = await original(route, body);
    if (route.endsWith('/segments')) {
      fixture.client.episode.segments.seg.evidence_acquisition = finalEvidence;
      fixture.c.state.events.push({ segment_id: 'seg', kind: 'evidence_acquisition_started', status: 'unknown' });
    }
    return result;
  };
  try {
    await fixture.c.start({ task: 'cube_lifting', text: '抬起' });
    await fixture.c.loopPromise;
    assert.equal(calls, 3);
    assert.equal(fixture.c.state.status, 'completed');
    assert.equal(fixture.c.state.evidence.status, 'available');
  } finally { await fixture.cleanup(); }
});

test('stale admission refreshes rather than terminating the closed loop', async () => {
  let calls = 0;
  const fixture = await setup(async () => ++calls === 1 ? observe : calls < 4 ? run : finish);
  const original = fixture.client.request.bind(fixture.client);
  let rejected = false;
  fixture.client.request = async (route, body) => {
    if (route.endsWith('/segments') && !rejected) {
      rejected = true;
      fixture.client.episode.revision++;
      const error = new Error('Stale revision'); error.code = 'STALE_REVISION'; throw error;
    }
    return original(route, body);
  };
  try {
    await fixture.c.start({ task: 'cube_lifting', text: '抬起红方块' }); await fixture.c.loopPromise;
    assert.equal(fixture.c.state.status, 'completed');
    assert.equal(fixture.client.calls.filter(c => c.route.endsWith('/segments')).length, 1);
  } finally { await fixture.cleanup(); }
});
test('SSE resumes after event ID and parses split chunks', async () => {
  let url;
  const client = new RuntimeClient('http://localhost:8120', 'token', async u => { url = u; return { ok: true, body: new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode('id: 4\ndata: {"id":')); controller.enqueue(new TextEncoder().encode('4}\n\n')); controller.close(); } }) }; });
  const received = [];
  await client.events('ep', 3, new AbortController().signal, e => received.push(e));
  assert.ok(url.endsWith('after=3'));
  assert.deepEqual(received, [{ id: 4 }]);
});

test('remaining task wall budget bounds a model that never responds', { timeout: 1500 }, async () => {
  let resolve;
  const late = new Promise(done => { resolve = done; });
  const fixture = await setup(async () => late);
  fixture.c.abort = new AbortController();
  fixture.c.taskStartedAt = Date.now() - 299_960;
  fixture.c.images = [];
  const epoch = fixture.c.epoch;
  try {
    await assert.rejects(fixture.c.decide(epoch, 'interpretation'), error => error.code === 'OPERATION_TIMEOUT');
    resolve(observe);
    await new Promise(done => setTimeout(done, 10));
    assert.equal(fixture.client.calls.filter(call => call.route === '/episodes').length, 0);
    assert.equal(fixture.c.state.messages.length, 0);
  } finally { resolve(observe); await fixture.cleanup(); }
});

for (const confirmHold of [true, false]) {
  test(`model timeout fences late code and reports protective hold ${confirmHold ? 'confirmed' : 'unconfirmed'}`, { timeout: 2000 }, async () => {
    const late = deferred(); let calls = 0, configurations = 0;
    const fixture = await setup(async () => ++calls === 1 ? observe : late.promise);
    fixture.c.configuration = async () => {
      if (++configurations === 2) fixture.c.taskStartedAt = Date.now()-299_950;
      return { active: {} };
    };
    const original = fixture.client.request.bind(fixture.client);
    fixture.client.request = async (route, body) => {
      if (route.endsWith('/cancel') && !confirmHold) throw new Error('offline hold endpoint');
      return original(route, body);
    };
    try {
      await fixture.c.start({ task: 'cube_lifting', text: '抬起' });
      await fixture.c.loopPromise;
      assert.equal(fixture.c.state.status, 'needs_attention');
      assert.equal(fixture.c.state.stop_confirmed, confirmHold);
      if (!confirmHold) assert.match(fixture.c.state.connection_error, /offline hold/);
      late.resolve(run);
      await new Promise(done => setTimeout(done, 10));
      assert.equal(fixture.client.calls.filter(call => call.route.endsWith('/segments')).length, 0);
    } finally { late.resolve(run); await fixture.cleanup(); }
  });
}
