// Engineering test harness. This script and Playwright are excluded from builds.
import { _electron } from '../node_modules/playwright/index.mjs';
import http from 'node:http';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'robot-desktop-'));
const output = path.resolve(process.argv[2] || path.join(root, '.tmp/robot-desktop-smoke'));
await fs.mkdir(output, { recursive: true });
const calls = [];
const model = http.createServer(async (request, response) => {
  let raw = ''; for await (const chunk of request) raw += chunk;
  const body = JSON.parse(raw);
  calls.push(body);
  const context = JSON.parse(body.messages[1].content.find(p => p.type === 'text').text);
  const task = context.scenario;
  const goal = task === 'cube_lifting' ? { holding: 'red cube' } : { on: ['red cube', 'green cube'], empty_gripper: true };
  const decision = context.mode === 'interpretation' ? { decision: 'observe', goal, message: '先观察场景，再执行目标。', remaining_steps: ['抓取', '验证'] }
    : !context.recent_attempts.length ? { decision: 'run_code', code: task === 'cube_lifting' ? 'pick("red cube")' : 'pick("red cube")\nplace_on("green cube")', subgoal: '执行测试动作', message: '正在执行可验证的短代码段。', remaining_steps: ['验证'] }
    : { decision: 'finish', message: '动作完成，请 Runtime 验证。', remaining_steps: [] };
  response.writeHead(200, { 'Content-Type': 'application/json' });
  response.end(JSON.stringify({ choices: [{ message: { content: JSON.stringify(decision) } }] }));
});
await new Promise(resolve => model.listen(0, '127.0.0.1', resolve));
const probe = http.createServer(); await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve)); const runtimePort = probe.address().port; await new Promise(resolve => probe.close(resolve));
const token = 'desktop-fixture-test-token';
const runtime = spawn(process.env.ROBOT_TEST_PYTHON || 'E:/miniconda3/python.exe', ['-m', 'agentic_cap.server', '--backend', 'fixture', '--port', String(runtimePort), '--records', path.join(directory, 'runtime')], {
  cwd: path.join(root, 'cap-runtime'), windowsHide: true, env: { ...process.env, CAP_RUNTIME_TOKEN: token }, stdio: ['ignore', 'pipe', 'pipe']
});
let runtimeLog = ''; runtime.stdout.on('data', x => runtimeLog += x); runtime.stderr.on('data', x => runtimeLog += x);
let electron;
try {
  for (let i = 0; i < 100; i++) {
    try { if ((await fetch(`http://127.0.0.1:${runtimePort}/health`, { headers: { Authorization: 'Bearer ' + token } })).ok) break; } catch {}
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  const env = { ...process.env, EMBUDDIES_DATA_DIR: path.join(directory, 'data'), EMBUDDIES_ALLOW_INSECURE_LOCALHOST: '1' }; delete env.ELECTRON_RUN_AS_NODE;
  electron = await _electron.launch({ executablePath: process.env.ROBOT_SMOKE_EXE || path.join(root, 'desktop/node_modules/electron/dist/electron.exe'), args: [process.env.ROBOT_SMOKE_APP_ROOT || path.join(root, 'desktop'), '--user-data-dir=' + path.join(directory, 'profile')], env });
  const page = await electron.firstWindow();
  await page.getByRole('heading', { name: '告诉机器人要做什么' }).waitFor();
  for (let i = 0; i < 100; i++) {
    const status = await page.evaluate(() => window.ROBOT_DESKTOP.runtimeStatus());
    if (status.state === 'ready') break;
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  const api = (route, body) => page.evaluate(({ route, body }) => window.ROBOT_DESKTOP.request({ path: route, method: body === undefined ? 'GET' : 'POST', body }), { route, body });
  await api('/robot/settings', { runtimeURL: `http://127.0.0.1:${runtimePort}`, runtimeToken: token, model: {
    activeChannel: 'robot-fixture', channelPatch: { id: 'robot-fixture', label: '协议测试模型', api: 'openai-completions', baseURL: `http://127.0.0.1:${model.address().port}/v1`, apiKey: 'fixture-key-only', models: ['fixture-vision'], model: 'fixture-vision' }
  } });
  const rows = [];
  for (const task of ['cube_lifting', 'cube_stack', 'cube_restack']) {
    await api('/robot/start', { task, text: task === 'cube_lifting' ? '抬起红色方块' : '把红色方块放到绿色方块上' });
    for (let i = 0; i < 150; i++) {
      const state = await api('/robot/state');
      if (state.status === 'completed' || state.status === 'needs_attention') break;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    const state = await api('/robot/state');
    if (state.status !== 'completed' || state.verification.status !== 'verified') console.log(JSON.stringify({ unexpected_state: state }));
    assert.equal(state.verification.status, 'verified');
    rows.push({ task, episode_id: state.episode.id, used: state.episode.used });
    if (task === 'cube_lifting') {
      await page.getByText('目标已验证', { exact: true }).waitFor();
      await page.screenshot({ path: path.join(output, 'robot-workbench.png'), fullPage: true });
    }
    const pack = await api('/robot/export');
    assert.ok(pack.runtime_zip_base64);
    await fs.writeFile(path.join(output, task + '.json'), JSON.stringify(pack));
    await api('/robot/close', {});
  }
  for (const route of ['/hardware', '/workspaces', '/resources', '/prompt/stream', '/browser', '/tools']) {
    await assert.rejects(() => api(route, {}), /未提供此路由/);
  }
  assert.equal(calls.length, 6); // Interpretation + action; Runtime verifies without another model decision.
  for (const row of rows) assert.equal(row.used.decisions, 2);
  await fs.writeFile(path.join(output, 'report.json'), JSON.stringify({ backend: 'fixture; not robot benchmark', model_calls: calls.length, tasks: rows, removed_routes_rejected: true }, null, 2));
  console.log(JSON.stringify({ output, tasks: rows.length, model_calls: calls.length, result: 'passed' }));
} finally {
  if (electron) await electron.close();
  runtime.kill();
  model.close();
  await fs.writeFile(path.join(output, 'runtime.log'), runtimeLog);
}
