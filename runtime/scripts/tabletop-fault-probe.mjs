// Real simulation protocol probes; the injected model is not a task-performance evaluation.
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {setTimeout as delay} from 'node:timers/promises';
import {once} from 'node:events';
import {fileURLToPath} from 'node:url';
import {RuntimeClient} from '../robot/runtime-client.mjs';
import {RobotCoordinator} from '../robot/coordinator.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const folder=process.argv[2];
if(!folder || !path.resolve(folder).startsWith(root+path.sep))throw new Error('Use a fresh Thor project folder');
await fs.mkdir(folder,{recursive:false});
const token=crypto.randomBytes(32).toString('hex');
const log=await fs.open(path.join(folder,'runtime.log'),'w');
const child=spawn(path.join(root,'.venv/bin/python'),['-u','-m','agentic_cap.server','--backend','cap','--host','127.0.0.1','--port','8121','--upstream',path.join(root,'third_party/cap-x'),'--records',path.join(folder,'physical')],
 {cwd:path.join(root,'cap-runtime'),stdio:['ignore',log.fd,log.fd],env:{...process.env,CAP_RUNTIME_TOKEN:token,MUJOCO_GL:'egl',PYOPENGL_PLATFORM:'egl',JAX_PLATFORMS:'cpu'}});
const exit=once(child,'exit');const client=new RuntimeClient('http://127.0.0.1:8121',token);
const checks={scope:'real simulation; injected model delay; no task-success claim'};let c,episode;
async function wait(fn,ms=60000){const end=Date.now()+ms;while(Date.now()<end){const value=await fn();if(value)return value;await delay(50);}throw new Error('Probe timed out');}
try{
 await wait(async()=>{try{return await client.request('/health');}catch{return false;}},30000);
 c=new RobotCoordinator({root:path.join(folder,'workbench'),client,configuration:async()=>({active:{}}),model:async(_channel,ctx)=>{
  const base={message:'Injected delay probe',subgoal:'Check continuous simulation',remaining_steps:[]};
  if(ctx.mode==='interpretation')return {...base,decision:'observe',goal:{all:[{predicate:'holding',object:'block_a'}]}};
  const before=await client.request(`/episodes/${c.state.episode.id}/observe`);const start=performance.now();await delay(5000);
  const after=await client.request(`/episodes/${c.state.episode.id}/observe`);
  checks.model_delay={wall_seconds:(performance.now()-start)/1000,simulation_seconds:after.simulation_time-before.simulation_time,
   control_steps:after.control_step-before.control_step};
  assert.ok(checks.model_delay.simulation_seconds>=4.5,JSON.stringify(checks.model_delay));
  return {...base,decision:'ask_user'};
 }});
 await c.start({task:'tabletop',scene:'tabletop_v1',seed:201,text:'Hold the blue block.'});await c.loopPromise;
 assert.equal(c.state.status,'awaiting_user');assert.ok(c.state.episode.assistance.hold_confirmed);await c.close();c.eventsAbort?.abort();
 const create={request_id:'physical-cancel',scene:'tabletop_v1',seed:201,goal:{all:[{predicate:'holding',object:'block_a'}]}};
 episode=await client.request('/episodes',create);const duplicate=await client.request('/episodes',create);assert.equal(duplicate.id,episode.id);
 checks.idempotent_creation=true;
 await wait(async()=>{const o=await client.request(`/episodes/${episode.id}/observe`);return o.system?.world?.facts?.['accessible:block_a']?.status==='verified'?o:false;});
 async function dispatch(id){
  const state=await client.request(`/episodes/${episode.id}`);
  await client.request(`/episodes/${episode.id}/plan`,{request_id:id+'-plan',revision:state.revision,plan:{nodes:[{id:'pick',skill:{name:'acquire_object',arguments:{object:'block_a'}}}]}});
  const o=await client.request(`/episodes/${episode.id}/observe`);const n=o.system.task.nodes.find(n=>n.status==='ready');assert.ok(n);
  const body={request_id:id,revision:state.revision,state_id:o.system.state_id,task_node:n.id,skill:{name:'acquire_object',arguments:{object:'block_a'}}};
  const accepted=await client.request(`/episodes/${episode.id}/skills`,body);
  const repeated=await client.request(`/episodes/${episode.id}/skills`,body);assert.deepEqual(repeated,accepted);
  checks.idempotent_execution=true;
  return body;
 }
 let body=await dispatch('cancel-operation');
 await wait(async()=>{const s=await client.request(`/episodes/${episode.id}`);return s.used.motion_steps>0&&s.active_segment?s:false;});
 const start=performance.now();const stopped=await client.request(`/episodes/${episode.id}/cancel`,{});const stopLatency=(performance.now()-start)/1000;assert.ok(stopped.stopped);
 let after=await client.request(`/episodes/${episode.id}`);await delay(500);let later=await client.request(`/episodes/${episode.id}`);
 assert.equal(after.used.motion_steps,later.used.motion_steps);
 await assert.rejects(client.request(`/episodes/${episode.id}/skills`,{...body,request_id:'late-after-stop'}),error=>error.code==='STALE_REVISION');
 checks.stop={confirmed:true,latency_seconds:stopLatency,motion_steps_after_ack:later.used.motion_steps-after.used.motion_steps,old_request_rejected:true};
 await wait(async()=>!(await client.request(`/episodes/${episode.id}`)).active_segment,10000);
 const before=later.used.motion_steps;body=await dispatch('change-operation');
 await wait(async()=>{const s=await client.request(`/episodes/${episode.id}`);return s.used.motion_steps>before&&s.active_segment?s:false;});
 const changed=await client.request(`/episodes/${episode.id}/goal`,{goal:{all:[{predicate:'holding',object:'cylinder_a'}]}});
 assert.equal(changed.id,episode.id);
 await assert.rejects(client.request(`/episodes/${episode.id}/skills`,{...body,request_id:'late-after-change'}),error=>error.code==='STALE_REVISION');
 checks.target_change={same_episode:true,old_request_rejected:true,revision:changed.revision};
 checks.status='passed';
}catch(error){checks.status='failed';checks.error=String(error.stack||error);process.exitCode=1;}
finally{
 if(c?.state.episode)await c.close().catch(()=>{});c?.eventsAbort?.abort();
 if(episode)await client.request(`/episodes/${episode.id}/close`,{}).catch(()=>{});
 child.kill('SIGINT');await Promise.race([exit,delay(10000)]);if(child.exitCode===null)child.kill('SIGKILL');await log.close();
 await fs.writeFile(path.join(folder,'checks.json'),JSON.stringify(checks,null,2));console.log(JSON.stringify(checks));
}
