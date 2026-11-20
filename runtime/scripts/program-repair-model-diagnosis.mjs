// Engineering-only handoff; no additional product routes, tool or model authority.
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { pathToFileURL } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { sourceManifest } from './robot-campaign.mjs';

export async function runProgramRepairDiagnosis(options, configuration) {
  const sources = await sourceManifest(options.sourceRoot);
  const pythonDigest = crypto.createHash('sha256').update(sources.files.filter(f=>f.path.startsWith('cap-runtime/'))
    .map(f=>f.path.slice('cap-runtime/'.length)+'\0'+f.sha256+'\n').join('')).digest('hex');
  const load = relative => import(pathToFileURL(path.join(options.sourceRoot,relative)).href);
  const { RobotCoordinator } = await load('runtime/robot/coordinator.mjs');
  const { RuntimeClient } = await load('runtime/robot/runtime-client.mjs');
  const { recoveryFacts, recordRecoveryAttempt, recoveryContext } = await load('runtime/robot/recovery.mjs');
  const token = (await fs.readFile(options.tokenFile,'utf8')).trim();
  const active = (await configuration()).active;
  const model = { model:active.model, api:active.api, baseURL:active.baseURL, reasoningEffort:active.reasoningEffort };
  if (JSON.stringify(model)!==JSON.stringify(options.expectedModel)) throw new Error('Model differs from frozen protocol');
  const entry = options.programRepairEpisodes[0];
  if (options.programRepairEpisodes.length!==1) throw new Error('One prepared case required');
  const client = new RuntimeClient(entry.url,token);
  const health = await client.request('/health');
  const diagnostic = health.diagnostic;
  if (health.source_digest!==pythonDigest || !diagnostic?.prepared || diagnostic.mode!==entry.mode) throw new Error('Prepared Runtime differs');
  for (const [relative, expected] of Object.entries(diagnostic.source_sha256)) {
    const data = await fs.readFile(path.join(options.sourceRoot,'cap-runtime',relative));
    if (crypto.createHash('sha256').update(data).digest('hex')!==expected) throw new Error('Prepared source differs: '+relative);
  }
  const episode = await client.request(`/episodes/${diagnostic.episode_id}`);
  if (episode.status!=='ready' || episode.active_segment) throw new Error('Prepared episode unavailable');
  await fs.mkdir(options.output,{recursive:true});
  await fs.writeFile(path.join(options.output,'protocol.json'),JSON.stringify({ sources,model,diagnostic,
    scope:'One fixed public failure handoff followed by autonomous code repair and original-goal execution; not end-to-end or statistical evidence' },null,2),{flag:'wx'});
  const folder = path.join(options.output,entry.mode);
  await fs.mkdir(folder,{recursive:true});
  const c = new RobotCoordinator({ root:path.join(folder,'records'), configuration,client });
  c.taskId=crypto.randomUUID(); c.recordDir=path.join(c.root,c.taskId);
  c.task=episode.task; c.seed=episode.seed; c.text='把绿色方块从红色方块上移开，再将红色方块放在绿色方块上，释放后空夹爪。'; c.images=[];
  c.taskStartedAt=Date.now()-episode.used.wall_seconds*1000;
  c.epoch=1; c.abort=new AbortController(); c.state.episode=episode;
  c.state.history=Object.values(episode.segments);
  c.state.messages.push({role:'user',text:c.text});
  for (const attempt of diagnostic.attempts) {
    const segment=episode.segments[attempt.segment_id];
    recordRecoveryAttempt(c.state.recovery,{ decision:{decision:'run_code',code:segment.code},segment,
      observation:attempt.observation,facts:recoveryFacts(episode,attempt.observation),revision:segment.revision });
  }
  const observation=await client.request(`/episodes/${episode.id}/observe`);
  const context=recoveryContext(c.state.recovery,episode,observation);
  if (context.recommendation!=='revise_code') throw new Error('Prepared program-repair route missing');
  const result={mode:entry.mode,task:episode.task,seed:episode.seed,episode_id:episode.id,
    started_at:Date.now()/1000,preparation_used:diagnostic.preparation_used,handoff_context:context};
  try {
    await c.record('diagnostic_model_attached',{episode_id:episode.id,preparation:diagnostic,progress:context});
    c.connectEvents(episode.id);
    c.launchLoop(c.epoch,()=>c.runExecution(c.epoch));
    await c.loopPromise;
    result.outcome=c.state.status; result.public_at_decision=c.state.verification; result.terminal_at=Date.now()/1000;
    await fs.writeFile(path.join(folder,'state-at-terminal.json'),JSON.stringify(c.state,null,2));
    await delay(1500);
  } catch(error) { result.outcome='infrastructure_error'; result.error=error.message; }
  finally {
    try { await c.close(); } catch(error) { result.close_error=error.message; }
    c.eventsAbort?.abort(); c.abort?.abort(); await c.recordQueue;
  }
  result.ended_at=Date.now()/1000; result.used_at_close=c.state.episode?.used;
  result.source_unchanged=(await sourceManifest(options.sourceRoot)).digest===sources.digest;
  await fs.writeFile(path.join(folder,'result.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify({mode:result.mode,outcome:result.outcome,episode_id:episode.id}));
  if (result.close_error || !result.source_unchanged) throw new Error('Prepared case closure/source requires audit; do not replay');
  return [result];
}
