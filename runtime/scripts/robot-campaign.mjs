// Engineering evaluation only. Never imported by the product application.
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

const texts = {
  cube_lifting: '抓起红色方块并保持抬起，不要释放。',
  cube_stack: '把红色方块放到绿色方块上，释放后空夹爪。',
  cube_restack: '把绿色方块从红色方块上移开，再将红色方块放在绿色方块上，释放后空夹爪。'
};
const hash = value => crypto.createHash('sha256').update(value).digest('hex');

export async function sourceManifest(root) {
  const files = [];
  for (const directory of ['runtime/robot', 'runtime/model-providers', 'cap-runtime/agentic_cap']) {
    for (const entry of await fs.readdir(path.join(root, directory), { withFileTypes: true })) {
      if (!entry.isFile() || !/\.(mjs|py)$/.test(entry.name) || entry.name.includes('.test.')) continue;
      const name = directory + '/' + entry.name;
      files.push({ path: name, sha256: hash(await fs.readFile(path.join(root, name))) });
    }
  }
  // Match Python's source filename order exactly. Locale collation orders
  // holding_watchdog.py before holding.py and produces a different digest.
  files.sort((a, b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0);
  return { files, digest: hash(JSON.stringify(files)) };
}

export function trialPlan(tasks, seeds, repeats = 1) {
  if (!Array.isArray(tasks) || !tasks.length || tasks.some(t => !Object.hasOwn(texts, t)) || new Set(tasks).size !== tasks.length) throw new Error('Invalid tasks');
  if (!Array.isArray(seeds) || !seeds.length || seeds.some(s => !Number.isInteger(s) || s < 0 || s >= 2**32) || new Set(seeds).size !== seeds.length) throw new Error('Invalid seeds');
  if (!Number.isInteger(repeats) || repeats < 1 || repeats > 100) throw new Error('Invalid repeats');
  const rows = [];
  // Seed-major order spreads task coverage over a time-limited allocation.
  for (const seed of seeds) for (const task of tasks) for (let repeat = 0; repeat < repeats; repeat++) rows.push({
    trial_id: `${task}-s${seed}-r${repeat}`, task, seed, repeat, text: texts[task]
  });
  return rows;
}

async function readJson(file) {
  return JSON.parse(await fs.readFile(file, 'utf8').catch(e => { if (e.code === 'ENOENT') return 'null'; throw e; }));
}
async function writeJson(file, value) {
  await fs.writeFile(file + '.tmp', JSON.stringify(value, null, 2));
  await fs.rename(file + '.tmp', file);
}

export function pairedPlan(schedule, variants) {
  if (!Array.isArray(schedule) || !schedule.length || !variants || typeof variants !== 'object') throw new Error('Missing paired schedule');
  if (Object.keys(variants).length < 2) throw new Error('Paired schedule requires at least two variants');
  const seen = new Set();
  const plan = schedule.map(row => {
    if (!row || !/^[a-z0-9-]+$/.test(row.pair_id) || !Object.hasOwn(variants, row.variant) ||
      !/^[a-z0-9-]+$/.test(row.variant)) throw new Error('Invalid paired schedule entry');
    trialPlan([row.task], [row.seed]);
    const key = row.pair_id + '/' + row.variant;
    if (seen.has(key)) throw new Error('Duplicate paired schedule entry');
    seen.add(key);
    return { pair_id: row.pair_id, variant: row.variant, task: row.task, seed: row.seed };
  });
  for (const id of new Set(plan.map(row => row.pair_id))) {
    const rows = plan.filter(row => row.pair_id === id);
    if (rows.length !== Object.keys(variants).length || rows.some(row => row.task !== rows[0].task || row.seed !== rows[0].seed)) {
      throw new Error('Paired variants must share task/seed and cover every variant');
    }
  }
  return plan;
}

export function assertRuntimeFeatures(health, expected) {
  if (expected === undefined) return;
  if (!expected || typeof expected !== 'object' || Array.isArray(expected)) throw new Error('Invalid expected Runtime features');
  for (const [name, value] of Object.entries(expected)) {
    if (!Object.hasOwn(health.features || {}, name) || health.features[name] !== value) {
      throw new Error('Runtime feature differs from frozen protocol: ' + name);
    }
  }
}

export async function runPairedCampaign(options, configuration) {
  const schedule = pairedPlan(options.schedule, options.variants);
  if (options.runtimeURLs && (Object.keys(options.runtimeURLs).length !== Object.keys(options.variants).length ||
      Object.keys(options.variants).some(name => typeof options.runtimeURLs[name] !== 'string'))) {
    throw new Error('Each paired variant requires its own explicit Runtime URL');
  }
  const variants = {};
  for (const [name, root] of Object.entries(options.variants)) variants[name] = await sourceManifest(root);
  const modelIdentity = async () => {
    const active = (await configuration()).active;
    return { model: active.model, api: active.api, baseURL: active.baseURL, reasoningEffort: active.reasoningEffort };
  };
  const model = await modelIdentity();
  if (options.expectedModel && JSON.stringify(model) !== JSON.stringify(options.expectedModel)) {
    throw new Error('Configured model differs from frozen paired protocol');
  }
  const manifest = { version: 1, schedule, variants, model, settle_milliseconds: options.settleMilliseconds ?? 1200,
    ...(options.runtimeURLs ? { runtime_urls: options.runtimeURLs } : {}),
    ...(options.runtimeFeatures ? { runtime_features: options.runtimeFeatures } : {}),
    scope: 'Sequential, counterbalanced paired diagnostic; no private feedback enters the policy.' };
  await fs.mkdir(options.output, { recursive: true });
  const file = path.join(options.output, 'paired-campaign.json');
  const prior = await readJson(file);
  if (prior && JSON.stringify(prior) !== JSON.stringify(manifest)) throw new Error('Paired campaign changed');
  if (!prior) await writeJson(file, manifest);
  const results = [];
  for (const row of schedule) {
    if (JSON.stringify(await modelIdentity()) !== JSON.stringify(model)) throw new Error('Model changed between paired trials');
    for (const [name, root] of Object.entries(options.variants)) {
      if ((await sourceManifest(root)).digest !== variants[name].digest) throw new Error('Paired variant changed: ' + name);
    }
    const result = await runCampaign({ ...options, sourceRoot: options.variants[row.variant],
      runtimeURL: options.runtimeURLs?.[row.variant] || options.runtimeURL,
      expectedRuntimeFeatures: options.runtimeFeatures?.[row.variant],
      output: path.join(options.output, row.pair_id, row.variant), tasks: [row.task], seeds: [row.seed], repeats: 1, maxTrials: 1 }, configuration);
    results.push({ ...row, ...result });
  }
  return { planned: schedule.length, results };
}

export async function runCampaign(options, configuration) {
  const { sourceRoot, output, runtimeURL, tokenFile, tasks, seeds, repeats = 1, maxTrials = 100, settleMilliseconds = 1200 } = options;
  const root = path.resolve(sourceRoot), out = path.resolve(output);
  const plan = trialPlan(tasks, seeds, repeats);
  const sources = await sourceManifest(root);
  const channel = (await configuration()).active;
  const model = { model: channel.model, api: channel.api, baseURL: channel.baseURL, reasoningEffort: channel.reasoningEffort };
  if (!channel.apiKey) throw new Error('Configured channel has no key');
  const modelDigest = hash(JSON.stringify(model));
  const { RobotCoordinator } = await import(pathToFileURL(path.join(root, 'runtime/robot/coordinator.mjs')).href);
  const { RuntimeClient } = await import(pathToFileURL(path.join(root, 'runtime/robot/runtime-client.mjs')).href);
  const client = new RuntimeClient(runtimeURL, (await fs.readFile(tokenFile, 'utf8')).trim());
  const health = await client.request('/health');
  // A remotely running Python service must publish its own code digest.
  // Merely hashing this Windows checkout says nothing about the deployed runtime.
  if (!health.source_digest || !health.backend) throw new Error('Runtime lacks campaign provenance; deploy the matching runtime first');
  const pythonDigest = hash(sources.files.filter(f => f.path.startsWith('cap-runtime/')).map(f =>
    f.path.slice('cap-runtime/'.length) + '\0' + f.sha256 + '\n').join(''));
  if (health.source_digest !== pythonDigest) throw new Error('Local/deployed Python source digest mismatch');
  assertRuntimeFeatures(health, options.expectedRuntimeFeatures);
  const manifest = { version: 1, plan, sources, model, model_digest: modelDigest,
    runtime: health, settle_milliseconds: settleMilliseconds, versions: process.versions,
    scoring: 'All admitted trials retained. Public completion and post-settle public check are separate. Private scoring is offline only.' };
  await fs.mkdir(out, { recursive: true });
  const saved = await readJson(path.join(out, 'campaign.json'));
  if (saved) {
    for (const key of ['plan', 'sources', 'model', 'runtime', 'settle_milliseconds']) if (JSON.stringify(saved[key]) !== JSON.stringify(manifest[key])) throw new Error('Campaign configuration/source changed: ' + key);
  } else await writeJson(path.join(out, 'campaign.json'), manifest);
  let count = 0;
  for (const trial of plan) {
    const folder = path.join(out, trial.trial_id);
    const resultFile = path.join(folder, 'result.json');
    if (await readJson(resultFile)) continue;
    if (count >= maxTrials) break;
    if ((await sourceManifest(root)).digest !== sources.digest) throw new Error('Source changed during campaign');
    const active = (await configuration()).active;
    if (hash(JSON.stringify({ model: active.model, api: active.api, baseURL: active.baseURL, reasoningEffort: active.reasoningEffort })) !== modelDigest) throw new Error('Model configuration changed during campaign');
    const started = await readJson(path.join(folder, 'started.json'));
    if (started) {
      const pointer = await readJson(path.join(folder, 'records/current.json'));
      if (!pointer?.state?.episode?.id) throw new Error('Interrupted trial has no authoritative episode identity; inspect before continuing: ' + trial.trial_id);
      const id = pointer.state.episode.id;
      const episode = await client.request(`/episodes/${id}`); // An offline server is NOT proof of termination.
      if (!['closed', 'interrupted'].includes(episode.status)) {
        await client.request(`/episodes/${id}/cancel`, {});
        await client.request(`/episodes/${id}/close`, {});
      }
      await writeJson(resultFile, { ...trial, outcome: 'interrupted', episode_id: id, started_at: started.time, ended_at: Date.now()/1000, replayed: false });
      count++;
      continue;
    }
    await fs.mkdir(folder, { recursive: true });
    const start = Date.now();
    await writeJson(path.join(folder, 'started.json'), { ...trial, time: start/1000 });
    const c = new RobotCoordinator({ root: path.join(folder, 'records'), configuration, client });
    let row;
    try {
      await c.start({ task: trial.task, seed: trial.seed, text: trial.text });
      await c.loopPromise;
      const terminal = Date.now()/1000;
      row = { ...trial, outcome: c.state.status, episode_id: c.state.episode?.id,
        started_at: start/1000, terminal_at: terminal, policy_seconds: terminal-start/1000,
        public_at_decision: structuredClone(c.state.verification), used_at_decision: c.state.episode?.used };
      if (c.state.episode?.status === 'ready') {
        await delay(settleMilliseconds);
        const state = await client.request(`/episodes/${c.state.episode.id}`);
        row.public_after_settle = await client.request(`/episodes/${state.id}/verify`, { revision: state.revision });
      }
      row.private_scoring = 'pending_offline';
      row.feedback_categories = c.state.history.map(h => h.feedback?.category).filter(Boolean);
      await writeJson(path.join(folder, 'state-at-terminal.json'), c.state);
    } catch (error) {
      row = { ...trial, outcome: 'infrastructure_error', episode_id: c.state.episode?.id, started_at: start/1000, error: error.message };
    } finally {
      try {
        if (c.state.episode) await c.close();
      } catch (error) { row.close_error = error.message; }
      c.eventsAbort?.abort();
      c.abort?.abort();
      await c.recordQueue;
    }
    row.ended_at = Date.now()/1000;
    row.total_seconds = row.ended_at-start/1000;
    row.used_at_close = c.state.episode?.used;
    row.source_unchanged = (await sourceManifest(root)).digest === sources.digest;
    await writeJson(resultFile, row);
    console.log(JSON.stringify({ trial_id: trial.trial_id, outcome: row.outcome, episode_id: row.episode_id, public: row.public_at_decision?.status, seconds: row.policy_seconds }));
    count++;
    if (!row.source_unchanged || row.close_error) throw new Error('Campaign halted after preserved trial: changed source or unconfirmed close');
  }
  return { attempted_this_run: count, planned: plan.length, output: out };
}
