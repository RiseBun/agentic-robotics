import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { RobotCoordinator } from './source/runtime/robot/coordinator.mjs';
import { RuntimeClient } from './source/runtime/robot/runtime-client.mjs';
import { sourceManifest } from './source/runtime/scripts/robot-campaign.mjs';
const root=path.dirname(fileURLToPath(import.meta.url));
const options=JSON.parse(await fs.readFile(path.join(root,'options.json'),'utf8'));
const resultsFile=path.join(root,'results.json');
if(await fs.stat(path.join(root,'started.json')).catch(()=>null))throw new Error('Already admitted; inspect original episodes, never replay');
const token=(await fs.readFile(options.tokenFile,'utf8')).trim();
const sources=await sourceManifest(options.sourceRoot);
const digest=crypto.createHash('sha256').update(sources.files.filter(f=>f.path.startsWith('cap-runtime/'))
  .map(f=>f.path.slice('cap-runtime/'.length)+'\0'+f.sha256+'\n').join('')).digest('hex');
const base=new RuntimeClient(options.runtimeURL,token);
const health=await base.request('/health');
if(health.source_digest!==digest)throw new Error('Python Runtime differs from local frozen source');
await fs.writeFile(path.join(root,'started.json'),JSON.stringify({cases:options.cases,source_digest:sources.digest,runtime_digest:digest}));
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return{promise,resolve};};
const results=[];
for(const spec of options.cases){
  const held=deferred(),release=deferred();
  let delayed=false,models=0;
  const client=new RuntimeClient(options.runtimeURL,token);
  const request=client.request.bind(client);
  client.request=async(route,...args)=>{
    const response=await request(route,...args);
    if(route.endsWith('/cancel')&&!delayed){
      delayed=true;held.resolve(response);await release.promise;
    }
    return response;
  };
  const model=async(_configuration,context)=>{
    models++;
    const green=context.user_goal.includes('绿色');
    return context.mode==='interpretation'
      ?{decision:'observe',goal:{holding:green?'green cube':'red cube'},message:'固定控制协议测试目标',remaining_steps:[]}
      :{decision:'ask_user',message:green?'新目标需要绿色方块证据':'旧目标需要红色方块证据',remaining_steps:[]};
  };
  const c=new RobotCoordinator({root:path.join(root,spec.mode+'-records'),configuration:async()=>({active:{}}),client,model});
  const row={...spec,passed:false};
  try{
    await c.start({task:'cube_lifting',text:'抬起红色方块',seed:spec.seed});
    const timer=new AbortController();
    const actualHold=await Promise.race([held.promise,delay(160_000,undefined,{signal:timer.signal})
      .then(()=>{throw new Error('Hold phase not reached');})]).finally(()=>timer.abort());
    if(!actualHold.stopped)throw new Error('Physical hold not confirmed before delaying response');
    row.episode_id=c.state.episode.id;
    const original=c.loopPromise,oldEpoch=c.epoch;
    const next=spec.mode==='stop'?c.stop():c.input({text:'改为抬起绿色方块'});
    if(c.epoch<=oldEpoch)throw new Error('New user input did not immediately fence old request');
    release.resolve();
    await next;await original;
    if(spec.mode==='goal_change')await c.loopPromise;
    const runtime=await base.request(`/episodes/${row.episode_id}`);
    row.state_at_terminal={status:c.state.status,assistance:c.state.assistance,goal:runtime.goal,used:runtime.used};
    row.passed=spec.mode==='stop'
      ?c.state.status==='stopped'&&c.state.assistance===undefined&&runtime.goal.holding==='red cube'
      :c.state.status==='awaiting_user'&&c.state.assistance?.message==='新目标需要绿色方块证据'
        &&runtime.goal.holding==='green cube'&&runtime.used.decisions===4;
    row.passed&&=Object.keys(runtime.segments).length===0;
    row.models=models;
    row.control_scope='no robot movement requested; physical owner-confirmed hold and stale workbench response fencing only';
  }catch(error){row.error=error.message;}
  finally{
    release.resolve();
    try{if(c.state.episode)await c.close();}catch(error){row.close_error=error.message;}
    c.eventsAbort?.abort();c.abort?.abort();await c.recordQueue;
    row.episode_id ||= c.state.episode?.id;
    row.closed=c.state.episode?.status==='closed';
    row.source_unchanged=(await sourceManifest(options.sourceRoot)).digest===sources.digest;
    results.push(row);await fs.writeFile(resultsFile,JSON.stringify(results,null,2));
    console.log(JSON.stringify({mode:row.mode,episode_id:row.episode_id,passed:row.passed,closed:row.closed,error:row.error}));
  }
  if(!row.source_unchanged||row.close_error)throw new Error('Stop after preserved source/close failure');
}
if(!results.every(r=>r.passed))process.exitCode=1;
