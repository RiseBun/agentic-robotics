// Engineering only. Continue an already prepared episode through the product
// execution loop; preparation and model actions share the same physical budget.
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { sourceManifest } from './robot-campaign.mjs';

async function corpusManifest(root) {
  const files = [];
  for (const folder of ['candidates', 'validated', 'revoked']) {
    const names = await fs.readdir(path.join(root, folder)).catch(e => { if (e.code === 'ENOENT') return []; throw e; });
    for (const name of names.sort()) {
      if (!/^[a-f0-9]{64}\.json$/.test(name)) throw new Error('Unexpected frozen corpus file');
      const relative = folder+'/'+name;
      files.push({ path: relative, sha256: crypto.createHash('sha256').update(await fs.readFile(path.join(root, relative))).digest('hex') });
    }
  }
  return { files, digest: crypto.createHash('sha256').update(JSON.stringify(files)).digest('hex') };
}

export async function runRetreatModelDiagnosis(options, configuration) {
  if (options.retreatEpisodes.some(e => e.experience) && !options.experienceRoot) throw new Error('Frozen experience corpus required');
  const sources = await sourceManifest(options.sourceRoot);
  const pythonDigest = crypto.createHash('sha256').update(sources.files.filter(f => f.path.startsWith('cap-runtime/'))
    .map(f => f.path.slice('cap-runtime/'.length)+'\0'+f.sha256+'\n').join('')).digest('hex');
  const { RobotCoordinator } = await import(pathToFileURL(path.join(options.sourceRoot, 'runtime/robot/coordinator.mjs')).href);
  const { RuntimeClient } = await import(pathToFileURL(path.join(options.sourceRoot, 'runtime/robot/runtime-client.mjs')).href);
  const { ExperienceStore } = await import(pathToFileURL(path.join(options.sourceRoot, 'runtime/robot/experience.mjs')).href);
  const corpus = options.experienceRoot ? await corpusManifest(options.experienceRoot) : null;
  const token = (await fs.readFile(options.tokenFile, 'utf8')).trim();
  const identity = active => ({ model: active.model, api: active.api, baseURL: active.baseURL, reasoningEffort: active.reasoningEffort });
  const model = identity((await configuration()).active);
  if (JSON.stringify(model) !== JSON.stringify(options.expectedModel)) throw new Error('Model differs from frozen protocol');
  await fs.mkdir(options.output, { recursive: true });
  const cases = [];
  for (const entry of options.retreatEpisodes) {
    const client = new RuntimeClient(entry.url, token);
    const health = await client.request('/health');
    if (health.source_digest !== pythonDigest || !health.diagnostic?.prepared || health.diagnostic.mode !== (entry.fault_mode || entry.mode)) {
      throw new Error('Prepared Runtime provenance mismatch: '+entry.mode);
    }
    const episode = await client.request(`/episodes/${health.diagnostic.episode_id}`);
    if (episode.status !== 'ready' || episode.active_segment) throw new Error('Prepared episode unavailable');
    cases.push({ ...entry, episode_id: episode.id, diagnostic: health.diagnostic });
  }
  const manifest = { sources, model, cases, experience_corpus: corpus, settle_milliseconds: 1250,
    scope: options.experienceRoot
      ? 'Fixed public failure preparation; frozen historical corpus present/absent. Bounded recovery transfer diagnosis, not end-to-end or statistical evidence.'
      : 'Fixed public preparation; actual product model/execution loop chooses recovery. No performance comparator.' };
  const manifestFile = path.join(options.output, 'protocol.json');
  try { await fs.writeFile(manifestFile, JSON.stringify(manifest, null, 2), { flag: 'wx' }); }
  catch (e) { if (e.code === 'EEXIST') throw new Error('Diagnosis already admitted; inspect original episodes, never replay'); throw e; }
  const results = [];
  for (const entry of cases) {
    if ((await sourceManifest(options.sourceRoot)).digest !== sources.digest ||
        JSON.stringify(identity((await configuration()).active)) !== JSON.stringify(model)) throw new Error('Frozen policy changed');
    const folder = path.join(options.output, entry.mode);
    await fs.mkdir(folder, { recursive: true });
    const client = new RuntimeClient(entry.url, token);
    const episode = await client.request(`/episodes/${entry.episode_id}`);
    if (corpus && (await corpusManifest(options.experienceRoot)).digest !== corpus.digest) throw new Error('Frozen experience corpus changed');
    const store = entry.experience ? new ExperienceStore(options.experienceRoot) : null;
    // Evaluation corpus stays immutable: test episodes cannot teach later cases.
    const experience = store ? { retrieve: q => store.retrieve(q), propose: async () => null } : null;
    const c = new RobotCoordinator({ root: path.join(folder, 'records'), configuration, client, experience });
    c.taskId = crypto.randomUUID(); c.recordDir = path.join(c.root, c.taskId);
    c.task = episode.task; c.seed = episode.seed; c.text = '把红色方块放到绿色方块上，释放后空夹爪。'; c.images = [];
    c.taskStartedAt = Date.now() - episode.used.wall_seconds*1000;
    c.epoch = 1; c.abort = new AbortController();
    c.state.episode = episode;
    c.state.history = Object.values(episode.segments);
    c.state.evidence = c.state.history.findLast(s => s.evidence_acquisition)?.evidence_acquisition || null;
    c.state.messages.push({ role: 'user', text: c.text });
    c.eventCursor = entry.diagnostic.handoff_event_id;
    let result = { mode: entry.mode, episode_id: episode.id, handoff_event_id: c.eventCursor,
      started_at: Date.now()/1000, preparation_used: episode.used };
    try {
      await c.record('diagnostic_model_attached', { episode_id: episode.id, preparation: entry.diagnostic });
      c.connectEvents(episode.id);
      c.launchLoop(c.epoch, () => c.runExecution(c.epoch));
      await c.loopPromise;
      result.outcome = c.state.status;
      result.public_at_decision = c.state.verification;
      result.terminal_at = Date.now()/1000;
      await fs.writeFile(path.join(folder, 'state-at-terminal.json'), JSON.stringify(c.state, null, 2));
      await delay(1250);
    } catch (error) { result.outcome = 'infrastructure_error'; result.error = error.message; }
    finally {
      try { await c.close(); } catch (error) { result.close_error = error.message; }
      c.eventsAbort?.abort(); c.abort?.abort(); await c.recordQueue;
    }
    result.ended_at = Date.now()/1000;
    result.used_at_close = c.state.episode?.used;
    result.source_unchanged = (await sourceManifest(options.sourceRoot)).digest === sources.digest;
    if (corpus) result.experience_corpus_unchanged = (await corpusManifest(options.experienceRoot)).digest === corpus.digest;
    await fs.writeFile(path.join(folder, 'result.json'), JSON.stringify(result, null, 2));
    results.push(result);
    console.log(JSON.stringify({ mode: entry.mode, outcome: result.outcome, episode_id: episode.id }));
    if (result.close_error || !result.source_unchanged || result.experience_corpus_unchanged === false) throw new Error('Original diagnosis retained; close/source/corpus must be audited before continuing');
  }
  return results;
}
