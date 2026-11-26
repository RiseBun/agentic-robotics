// Actual coordinator -> authenticated HTTP -> Python Episode/interpreter fixture.
// This is a protocol/integration gate, never a physical robot benchmark.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { execFile, spawn } from 'node:child_process';
import { promisify } from 'node:util';
import { fileURLToPath } from 'node:url';
import { once } from 'node:events';
import { setTimeout as delay } from 'node:timers/promises';
import { RuntimeClient } from './runtime-client.mjs';
import { RobotCoordinator } from './coordinator.mjs';
import { parseDecision, promptForContext } from './model.mjs';
import { CONTRACT_FILES, digest, recoveryContract } from './experience.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const token = 'unified-system-protocol-fixture-token';
const python = process.env.ROBOT_TEST_PYTHON || 'python';
test('Python Runtime and closed-task experience importer compute the same semantic contract', async () => {
  const files = await Promise.all(CONTRACT_FILES.map(async name => ({
    path: 'cap-runtime/agentic_cap/'+name,
    sha256: digest(await fs.readFile(path.join(root, 'cap-runtime/agentic_cap', name), 'utf8'))
  })));
  const { stdout } = await promisify(execFile)(python,
    ['-c', 'import json; from agentic_cap.server import runtime_provenance; print(json.dumps(runtime_provenance("cap")))'],
    { cwd: path.join(root, 'cap-runtime'), windowsHide: true });
  const provenance = JSON.parse(stdout);
  assert.equal(provenance.recovery_contract, recoveryContract(files));
  for (const name of ['robot_system.py', 'skills.py', 'task_recipes.py', 'placement_retry.py']) {
    assert.ok(CONTRACT_FILES.includes(name));
    assert.throws(() => recoveryContract(files.filter(f => !f.path.endsWith('/'+name))));
  }
});
async function setup() {
  const folder = await fs.mkdtemp(path.join(os.tmpdir(), 'robot-system-'));
  const program = `import json,sys\nfrom agentic_cap.core import Manager\nfrom agentic_cap.server import serve\nfrom test_robot_system import SystemFixture\ns=serve(Manager(sys.argv[1],SystemFixture),'127.0.0.1',0,sys.argv[2])\nprint(json.dumps({'port':s.server_address[1]}),flush=True)\ns.serve_forever()`;
  const child = spawn(python, ['-u', '-c', program, path.join(folder, 'runtime'), token], {
    cwd: path.join(root, 'cap-runtime'), windowsHide: true,
    env: { ...process.env, PYTHONPATH: [path.join(root, 'cap-runtime'), path.join(root, 'cap-runtime/tests')].join(path.delimiter) },
    stdio: ['ignore', 'pipe', 'pipe']
  });
  let out = '', stderr = '';
  child.stdout.on('data', value => { out += value; });
  child.stderr.on('data', value => { stderr += value; });
  let failure; child.on('error', error => { failure = error; });
  for (let i = 0; i < 150 && !out.includes('\n'); i++) {
    if (failure || child.exitCode !== null) throw failure || new Error(stderr || 'Python fixture exited');
    await delay(100);
  }
  if (!out.includes('\n')) { child.kill(); throw new Error('Python fixture startup timeout: '+stderr); }
  const url = `http://127.0.0.1:${JSON.parse(out.split('\n')[0]).port}`;
  const client = new RuntimeClient(url, token);
  const cleanup = async () => {
    if (child.exitCode === null) { const exited = once(child, 'exit'); child.kill(); await exited; }
    await fs.rm(folder, { recursive: true, force: true });
  };
  return { client, folder, cleanup };
}

const interpret = { decision: 'observe', message: '明确目标', remaining_steps: [],
  goal: { on: ['red cube', 'green cube'], empty_gripper: true } };
test('registered recovery decision binds the Runtime node without accepting injected code', () => {
  const decision = { decision: 'run_skill', message: '沿已记录路径撤回', remaining_steps: [],
    state_id: 'a'.repeat(64), task_node: '2:recover',
    skill: { name: 'retreat_from_descent', arguments: { object: 'red cube' } } };
  assert.deepEqual(parseDecision(JSON.stringify(decision)).skill, decision.skill);
  assert.match(promptForContext({ robot_system: { contract_version: 1 } }), /retreat_from_descent/);
  assert.throws(() => parseDecision(JSON.stringify({ ...decision, code: 'open_gripper()' })));
  assert.throws(() => parseDecision(JSON.stringify({ ...decision, task_node: '2:shell' })));
});
const choose = context => {
  const system = context.robot_system;
  const node = system.task.nodes.find(n => n.status === 'ready' && (n.kind === 'clear' || n.skills.some(s => ['acquire_object', 'place_object'].includes(s))));
  assert.ok(node, 'Runtime must expose an applicable task node');
  if (node.kind === 'clear') return { decision: 'run_code', message: '移开已持有的遮挡物', remaining_steps: [],
    task_ref: { state_id: system.state_id, task_node: node.id }, code: 'place_on("table")' };
  const name = node.skills[0];
  return { decision: 'run_skill', message: '执行已注册技能', remaining_steps: [],
    state_id: system.state_id, task_node: node.id,
    skill: { name, arguments: node.arguments } };
};

test('model assistance closes the Runtime dispatch gate until a new goal, in one environment', async () => {
  const { client, folder, cleanup } = await setup();
  let mode = 'ask';
  const coordinator = new RobotCoordinator({ root: path.join(folder, 'help-workbench'), client,
    configuration: async () => ({ active: {} }),
    model: async (_channel, context) => context.mode === 'interpretation'
      ? { decision: 'observe', goal: { holding: 'red cube' }, message: '明确目标', remaining_steps: [] }
      : mode === 'ask' ? { decision: 'ask_user', message: '请恢复观测', remaining_steps: [] } : choose(context) });
  try {
    await coordinator.start({ task: 'cube_lifting', text: '抬起红色方块' });
    await coordinator.loopPromise;
    assert.equal(coordinator.state.status, 'awaiting_user');
    const id = coordinator.state.episode.id;
    const paused = await client.request(`/episodes/${id}`);
    assert.equal(paused.system.task.execution.phase, 'awaiting_user');
    assert.equal(paused.assistance.hold_confirmed, true);
    assert.equal(paused.used.calls, 0);
    await assert.rejects(client.request(`/episodes/${id}/segments`, { request_id: 'late-foreign-client',
      revision: paused.revision, subgoal: 'late', code: 'pick("red cube")',
      task_ref: { state_id: paused.system.state_id, task_node: '1:acquire' } }), { code: 'AWAITING_USER' });
    mode = 'run';
    await coordinator.input({ text: '观测已恢复，继续抬起红色方块', images: [] });
    await coordinator.loopPromise;
    assert.equal(coordinator.state.episode.id, id);
    assert.equal(coordinator.state.status, 'completed', coordinator.state.messages.at(-1)?.text);
    const resumed = await client.request(`/episodes/${id}`);
    assert.equal(resumed.assistance, null);
    assert.equal(resumed.system.goal_version, 2);
    assert.equal(resumed.used.calls, 1);
    assert.ok(resumed.used.decisions > paused.used.decisions);
    assert.deepEqual(Object.values(resumed.segments).map(s => s.task_binding.node_id), ['2:acquire']);
    const pack = await coordinator.export();
    assert.ok(pack.workbench_jsonl.includes('assistance_requested'));
  } finally { coordinator.eventsAbort?.abort(); coordinator.abort?.abort(); await cleanup(); }
});

test('restack mixes blocker skill, bound CaP clearing and target skills in one task/export', async () => {
  const { client, folder, cleanup } = await setup();
  const coordinator = new RobotCoordinator({ root: path.join(folder, 'mixed-workbench'), client,
    configuration: async () => ({ active: {} }),
    model: async (_channel, context) => context.mode === 'interpretation' ? interpret : parseDecision(JSON.stringify(choose(context))) });
  try {
    await coordinator.start({ task: 'cube_restack', text: '先移开绿色方块，再将红色方块放在绿色方块上' });
    await coordinator.loopPromise;
    assert.equal(coordinator.state.status, 'completed', coordinator.state.messages.at(-1)?.text);
    const state = await client.request(`/episodes/${coordinator.state.episode.id}`);
    const segments = Object.values(state.segments);
    assert.deepEqual(segments.map(s => s.task_binding.node_id), ['1:clear_acquire', '1:clear', '1:acquire', '1:place']);
    assert.ok(segments.every(s => s.node_verification.status === 'verified'));
    assert.equal(segments[0].skill.arguments.object, 'green cube');
    assert.equal(segments[1].skill, undefined);
    assert.notEqual(segments[1].goal_verification.status, 'verified');
    assert.equal(state.used.decisions, 5);
    assert.equal(state.used.calls, 4);
    const exported = await coordinator.export();
    assert.ok(exported.workbench_jsonl.includes('"node_verification"'));
    assert.ok(exported.workbench_jsonl.includes('"task_ref"'));
    await coordinator.close();
  } finally {
    coordinator.eventsAbort?.abort(); coordinator.abort?.abort();
    await coordinator.recordQueue;
    await cleanup();
  }
});

test('coordinator rejects unbound model code when Runtime publishes the task contract', async () => {
  const { client, folder, cleanup } = await setup();
  const coordinator = new RobotCoordinator({ root: path.join(folder, 'unbound-model'), client,
    configuration: async () => ({ active: {} }), model: async (_channel, context) => context.mode === 'interpretation'
      ? interpret : { decision: 'run_code', message: 'unbound', remaining_steps: [], code: 'pick("red cube")' } });
  try {
    await coordinator.start({ task: 'cube_stack', text: '红块放到绿块上' });
    await coordinator.loopPromise;
    const state = await client.request(`/episodes/${coordinator.state.episode.id}`);
    assert.equal(coordinator.state.status, 'needs_attention');
    assert.match(coordinator.state.messages.at(-1).text, /必须引用 Runtime 当前任务节点/);
    assert.equal(state.used.calls, 0);
    assert.equal(Object.keys(state.segments).length, 0);
    await coordinator.close();
  } finally {
    coordinator.eventsAbort?.abort(); coordinator.abort?.abort();
    await coordinator.recordQueue;
    await cleanup();
  }
});

test('unified task -> model skill -> real HTTP/interpreter -> public verification -> export', async () => {
  const { client, folder, cleanup } = await setup();
  const contexts = [];
  const coordinator = new RobotCoordinator({ root: path.join(folder, 'workbench'), client,
    configuration: async () => ({ active: {} }),
    model: async (_channel, context) => {
      if (context.mode === 'interpretation') return interpret;
      contexts.push(context);
      assert.equal(context.robot_system, context.latest_observation.system);
      assert.match(promptForContext(context), /authoritative task/);
      const choice = parseDecision(JSON.stringify(choose(context)));
      // New sensor packets during thinking must not invalidate unchanged dependencies.
      await delay(250);
      return choice;
    } });
  try {
    await coordinator.start({ task: 'cube_stack', text: '红块放到绿块上' });
    await coordinator.loopPromise;
    assert.equal(coordinator.state.status, 'completed', coordinator.state.messages.at(-1)?.text);
    const episode = await client.request(`/episodes/${coordinator.state.episode.id}`);
    assert.equal(episode.used.decisions, 3);
    assert.equal(episode.used.calls, 2);
    assert.equal(Object.values(episode.segments).length, 2);
    assert.ok(Object.values(episode.segments).every(s => s.skill && s.task_binding));
    assert.equal(contexts.length, 2);
    assert.equal(contexts[0].robot_system.world.facts['holding:red cube'].status, 'failed');
    assert.equal(contexts[1].robot_system.world.facts['holding:red cube'].status, 'verified');
    const view = await coordinator.view();
    assert.equal(view.observation.system.task.status, 'verified');
    const exported = await coordinator.export();
    assert.ok(exported.workbench_jsonl.includes('"task_binding"'));
    assert.ok(exported.runtime_zip_base64);
    await coordinator.close();
  } finally {
    coordinator.eventsAbort?.abort(); coordinator.abort?.abort();
    await coordinator.recordQueue;
    await cleanup();
  }
});

test('HTTP rejects old goal bindings and altered skill arguments without dispatch', async () => {
  const { client, cleanup } = await setup();
  try {
    let episode = await client.request('/episodes', { request_id: 'bindings', task: 'cube_stack', seed: 0, goal: interpret.goal });
    for (let i=0; i<100 && episode.status !== 'ready'; i++) { await delay(20); episode = await client.request(`/episodes/${episode.id}`); }
    const observation = await client.request(`/episodes/${episode.id}/observe`);
    const payload = { request_id: 'acquire', revision: episode.revision,
      state_id: observation.system.state_id, task_node: '1:acquire',
      skill: { name: 'acquire_object', arguments: { object: 'green cube' } } };
    await assert.rejects(client.request(`/episodes/${episode.id}/skills`, payload), /current task goal/);
    await client.request(`/episodes/${episode.id}/goal`, { goal: { holding: 'green cube' } });
    payload.skill.arguments.object = 'red cube';
    await assert.rejects(client.request(`/episodes/${episode.id}/skills`, payload), e => e.code === 'STALE_REVISION');
    const state = await client.request(`/episodes/${episode.id}`);
    assert.equal(state.used.calls, 0);
    assert.equal(Object.keys(state.segments).length, 0);
    await client.request(`/episodes/${episode.id}/close`, {});
  } finally { await cleanup(); }
});
