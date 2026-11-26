import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createConfigStore } from '../config-store.mjs';
import { createSecretStore } from '../secret-store.mjs';
import { publicChannel } from '../channels.mjs';
import { RuntimeClient } from './runtime-client.mjs';
import { RobotCoordinator } from './coordinator.mjs';
import { ExperienceStore } from './experience.mjs';
import { sourceDigest, deploymentStatus } from './versions.mjs';

const runtimeRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const dataRoot = process.env.EMBUDDIES_DATA_DIR || path.join(runtimeRoot, 'data');
const config = createConfigStore(runtimeRoot, dataRoot);
const secrets = createSecretStore(runtimeRoot, dataRoot);
const settingsFile = path.join(dataRoot, 'robot-settings.json');
const loadedWorkbench = await sourceDigest(path.join(runtimeRoot, 'robot'), '.mjs');
let coordinator;
let settings;
async function readSettings() {
  return JSON.parse(await fs.readFile(settingsFile, 'utf8').catch(error => {
    if (error.code === 'ENOENT') return '{"runtimeURL":"http://127.0.0.1:8120"}';
    throw error;
  }));
}
async function client() { return new RuntimeClient(settings.runtimeURL, await secrets.get('robot-runtime')); }
async function publicSettings() {
  const channels = await config.read();
  const experimentalModes = [];
  let runningRuntime;
  try { runningRuntime = (await (await client()).request('/health', undefined, AbortSignal.timeout(2000))).source_digest; } catch { /* Unreachable is unknown, never current. */ }
  const deployment = deploymentStatus({ runningRuntime,
    installedRuntime: await sourceDigest(path.resolve(runtimeRoot, '../cap-runtime/agentic_cap'), '.py', 'agentic_cap/').catch(()=>undefined),
    loadedWorkbench, installedWorkbench: await sourceDigest(path.join(runtimeRoot, 'robot'), '.mjs').catch(()=>undefined) });
  if (process.env.EMBUDDIES_GENERIC_TABLETOP === '1') {
    try {
      const runtime = await client();
      const description = await runtime.request('/system');
      if (description.schema === 2) experimentalModes.push('tabletop');
    } catch { /* Old/unavailable Runtime must not expose an unusable mode. */ }
  }
  return { deployment, experimentalModes, runtimeURL: settings.runtimeURL, hasRuntimeToken: Boolean(await secrets.get('robot-runtime')),
    activeChannel: channels.channel, channels: Object.values(channels.channels).map(publicChannel) };
}
export async function startApplication() {
  settings = await readSettings();
  coordinator = new RobotCoordinator({ root: path.join(dataRoot, 'robot-tasks'), configuration: () => config.read(), client: await client(),
    experience: process.env.EMBUDDIES_EXPERIENCE === '1' ? new ExperienceStore(path.join(dataRoot, 'robot-experience')) : null });
  await coordinator.restore();
  return { service: 'embuddies-robot', protocol: 1, tools: ['observe', 'propose_plan', 'run_code', 'run_skill', 'ask_user', 'finish'] };
}
export async function closeApplication() {
  // Stop on desktop shutdown; never silently resume model or physical execution.
  if (coordinator?.state.episode && !['closed', 'interrupted'].includes(coordinator.state.episode.status)) await coordinator.stop();
  coordinator?.eventsAbort?.abort();
}
export async function invokeApplication(request) {
  const method = request.method || 'GET';
  const route = request.path;
  const body = request.body || {};
  if (route === '/health' && method === 'GET') return { ok: true, service: 'embuddies-robot', tools: ['observe', 'propose_plan', 'run_code', 'run_skill', 'ask_user', 'finish'] };
  if (route === '/robot/settings' && method === 'GET') return publicSettings();
  if (route === '/robot/settings' && method === 'POST') {
    if (coordinator.state.episode && !['closed', 'interrupted'].includes(coordinator.state.episode.status)) throw new Error('结束当前场景后再修改连接配置');
    if (body.model) {
      if (Object.keys(body.model).some(key => !['activeChannel', 'channelPatch'].includes(key))) throw new Error('不支持的模型配置字段');
      await config.update(body.model);
    }
    if (body.runtimeURL) {
      new RuntimeClient(body.runtimeURL, '');
      settings.runtimeURL = body.runtimeURL;
    }
    if (Object.hasOwn(body, 'runtimeToken')) await secrets.set('robot-runtime', body.runtimeToken);
    await fs.mkdir(dataRoot, { recursive: true });
    const temp = settingsFile + '.tmp';
    await fs.writeFile(temp, JSON.stringify(settings, null, 2));
    await fs.rename(temp, settingsFile);
    coordinator.client = await client();
    return publicSettings();
  }
  if (route === '/robot/state' && method === 'GET') return coordinator.view();
  if (route === '/robot/start' && method === 'POST' && body.task === 'tabletop' && process.env.EMBUDDIES_GENERIC_TABLETOP !== '1') throw new Error('Generic tabletop is an opt-in experimental mode');
  if (route === '/robot/start' && method === 'POST') return coordinator.start(body);
  if (route === '/robot/input' && method === 'POST') return coordinator.input(body);
  if (route === '/robot/stop' && method === 'POST') return coordinator.stop();
  if (route === '/robot/close' && method === 'POST') return coordinator.close();
  if (route === '/robot/export' && method === 'GET') return coordinator.export();
  if (route === '/robot/records' && method === 'GET') return fs.readdir(path.join(dataRoot, 'robot-tasks')).catch(error => { if (error.code === 'ENOENT') return []; throw error; });
  throw new Error('机器人版本未提供此路由');
}
