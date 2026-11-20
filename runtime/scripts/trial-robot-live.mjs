// Actual configured model + actual CaP runtime. Credentials stay in Electron.
import { _electron } from '../node_modules/playwright/index.mjs';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const output = path.resolve(process.argv[2]);
const token = (await fs.readFile(process.argv[3], 'utf8')).trim();
const data = path.join(process.env.LOCALAPPDATA, 'embuddies/data');
await fs.mkdir(output, { recursive: true });
const env = { ...process.env, EMBUDDIES_DATA_DIR: data }; delete env.ELECTRON_RUN_AS_NODE;
const app = await _electron.launch({ executablePath: path.join(root, 'desktop/node_modules/electron/dist/electron.exe'),
  args: [path.join(root, 'desktop'), '--user-data-dir=' + path.join(process.env.LOCALAPPDATA, 'embuddies/profile')], env });
try {
  const page = await app.firstWindow();
  await page.getByRole('heading', { name: '告诉机器人要做什么' }).waitFor();
  let ready = false;
  for (let i = 0; i < 600; i++) {
    const status = await page.evaluate(() => window.ROBOT_DESKTOP.runtimeStatus());
    if (status.state === 'ready') { ready = true; break; }
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  if (!ready) throw new Error('Desktop runtime did not become ready within 60 seconds');
  const api = (route, body) => page.evaluate(({ route, body }) => window.ROBOT_DESKTOP.request({ path: route, method: body === undefined ? 'GET' : 'POST', body }), { route, body });
  const settings = await api('/robot/settings');
  const active = settings.channels.find(c => c.id === settings.activeChannel);
  console.log(JSON.stringify({ channel: active.id, model: active.model, hasKey: active.hasKey }));
  if (!active.hasKey) throw new Error('Current channel has no key');
  const previous = await api('/robot/state');
  if (previous.episode && !['closed', 'interrupted'].includes(previous.episode.status)) {
    if (process.argv.includes('--close-previous-trial') && ['stopped', 'completed', 'needs_attention'].includes(previous.status)) await api('/robot/close', {});
    else throw new Error('Existing robot task is active; trial will not replace it');
  }
  await api('/robot/settings', { runtimeURL: 'http://127.0.0.1:18121', runtimeToken: token });
  const rows = [];
  const selected = process.env.ROBOT_TRIAL_TASKS?.split(',');
  for (const [task, text] of [
    ['cube_lifting', '抓起红色方块并保持抬起，不要释放。'],
    ['cube_stack', '把红色方块放到绿色方块上，释放后空夹爪。'],
    ['cube_restack', '把绿色方块从红色方块上移开，再将红色方块放在绿色方块上，释放后空夹爪。']
  ]) {
    if (selected && !selected.includes(task)) continue;
    const start = Date.now();
    await api('/robot/start', { task, text, seed: 0 });
    let state;
    for (let i = 0; i < 420; i++) {
      state = await api('/robot/state');
      if (['completed', 'needs_attention', 'awaiting_user'].includes(state.status)) break;
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    if (!['completed', 'needs_attention', 'awaiting_user'].includes(state.status)) await api('/robot/stop', {});
    state = await api('/robot/state');
    const row = { task, status: state.status, verification: state.verification, episode_id: state.episode?.id,
      used: state.episode?.used, messages: state.messages, elapsed_seconds: (Date.now()-start)/1000 };
    rows.push(row);
    await fs.writeFile(path.join(output, 'live-report.json'), JSON.stringify({ channel: active.id, model: active.model, rows }, null, 2));
    await page.screenshot({ path: path.join(output, task + '.png'), fullPage: true });
    console.log(JSON.stringify(row));
    await api('/robot/close', {});
    // Full raw artifacts remain at the runtime records directory. Optional
    // download is separate from policy latency and cannot delay the next trial.
    if (process.argv.includes('--download-records')) await fs.writeFile(path.join(output, task + '.json'), JSON.stringify(await api('/robot/export')));
  }
} finally { await app.close(); }
