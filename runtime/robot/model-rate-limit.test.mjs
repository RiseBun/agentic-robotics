import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {setTimeout as delay} from 'node:timers/promises';
import {modelHTTPError} from './model.mjs';
import {RobotCoordinator} from './coordinator.mjs';
import {RuntimeClient} from './runtime-client.mjs';
const rate=(ms=2)=>Object.assign(new Error('HTTP 429'),{code:'MODEL_RATE_LIMITED',status:429,retryAfterMs:ms});
const base={message:'rate probe',subgoal:'probe',remaining_steps:[]};
async function fixture(run){
 const dir=await fs.mkdtemp(path.join(os.tmpdir(),'model-rate-'));const token='rate-test-token';
 const code=`from agentic_cap.core import Manager
from agentic_cap.server import serve
from agentic_cap.tabletop_fixture import TabletopFixtureBackend
import sys,json
s=serve(Manager(sys.argv[1],TabletopFixtureBackend),'127.0.0.1',0,sys.argv[2])
print(json.dumps({'port':s.server_address[1]}),flush=True)
s.serve_forever()`;
 const child=spawn(process.env.ROBOT_TEST_PYTHON||'python',['-u','-c',code,path.join(dir,'runtime'),token],{cwd:path.resolve('cap-runtime'),env:{...process.env,PYTHONPATH:path.resolve('cap-runtime')},stdio:['ignore','pipe','pipe']});
 let output='',errors='',c;child.stdout.on('data',b=>output+=b);child.stderr.on('data',b=>errors+=b);
 try{
  for(let i=0;i<120&&!output.includes('\n');i++){if(child.exitCode!==null)throw new Error(errors);await delay(20);}
  assert.ok(output.includes('\n'),errors);
  const client=new RuntimeClient('http://127.0.0.1:'+JSON.parse(output.split('\n')[0]).port,token);
  await run({dir,client,set:value=>c=value});
 }finally{if(c?.state.episode)await c.close().catch(()=>{});c?.eventsAbort?.abort();child.kill();await delay(30);await fs.rm(dir,{recursive:true,force:true});}
}

test('HTTP 429 honors numeric/date Retry-After while other errors are not retried',()=>{
 assert.equal(modelHTTPError(new Response('',{status:429,headers:{'Retry-After':'2'}})).retryAfterMs,2000);
 const now=Date.parse('2026-11-24T00:00:00Z');
 assert.equal(modelHTTPError(new Response('',{status:429,headers:{'Retry-After':'Tue, 24 Nov 2026 00:00:03 GMT'}}),now).retryAfterMs,3000);
 assert.equal(modelHTTPError(new Response('',{status:403})).code,undefined);
 assert.equal(modelHTTPError(new Response('',{status:429,headers:{'Retry-After':'invalid'}})).retryAfterMs,undefined);
});

test('interpretation and execution retries each consume a model decision in one episode',async()=>fixture(async({dir,client,set})=>{
 let interpretation=0,execution=0,calls=0;
 const c=new RobotCoordinator({root:path.join(dir,'workbench'),client,configuration:async()=>({active:{}}),model:async(_channel,ctx)=>{
  calls++;
  if(ctx.mode==='interpretation'){
   if(interpretation++===0)throw rate();
   return {...base,decision:'observe',goal:{all:[{predicate:'holding',object:'block_a'}]}};
  }
  if(execution++===0)throw rate();
  if(!ctx.robot_system.task.nodes.length)return {...base,decision:'propose_plan',plan:{nodes:[{id:'pick',skill:{name:'acquire_object',arguments:{object:'block_a'}}}]}};
  const n=ctx.robot_system.task.nodes.find(n=>n.status==='ready');assert.ok(n);
  return {...base,decision:'run_skill',state_id:ctx.robot_system.state_id,task_node:n.id,skill:{name:'acquire_object',arguments:n.arguments}};
 }});set(c);
 await c.start({task:'tabletop',text:'hold blue',seed:201});await c.loopPromise;
 assert.equal(c.state.status,'completed',JSON.stringify(c.state.messages));
 assert.equal(calls,5);assert.equal(c.state.episode.used.decisions,5);
 assert.equal(Object.keys(c.state.episode.segments).length,1);
}));

test('stop while waiting for model quota fences every later decision and robot dispatch',async()=>fixture(async({dir,client,set})=>{
 let reached;const waiting=new Promise(resolve=>reached=resolve);let execution=0;
 const c=new RobotCoordinator({root:path.join(dir,'workbench'),client,configuration:async()=>({active:{}}),model:async(_channel,ctx)=>{
  if(ctx.mode==='interpretation')return {...base,decision:'observe',goal:{all:[{predicate:'holding',object:'block_a'}]}};
  execution++;reached();throw rate(500);
 }});set(c);await c.start({task:'tabletop',text:'hold blue',seed:201});await waiting;
 await delay(20);await c.stop();await c.loopPromise;
 assert.equal(execution,1);assert.equal(Object.keys(c.state.episode.segments).length,0);assert.equal(c.state.status,'stopped');
}));

test('persistent rate limiting has bounded attempts and does not reset task budget',async()=>fixture(async({dir,client,set})=>{
 let execution=0;
 const c=new RobotCoordinator({root:path.join(dir,'workbench'),client,configuration:async()=>({active:{}}),model:async(_channel,ctx)=>{
  if(ctx.mode==='interpretation')return {...base,decision:'observe',goal:{all:[{predicate:'holding',object:'block_a'}]}};
  execution++;throw rate();
 }});set(c);await c.start({task:'tabletop',text:'hold blue',seed:201});await c.loopPromise;
 assert.equal(c.state.status,'needs_attention');assert.equal(execution,3);assert.equal(c.state.episode.used.decisions,4);
 assert.equal(Object.keys(c.state.episode.segments).length,0);assert.equal(c.state.stop_confirmed,true);
}));
