import test from 'node:test';
import assert from 'node:assert/strict';
import {parseDecision,promptForContext} from './model.mjs';
import {RobotCoordinator} from './coordinator.mjs';
const context={scenario:'tabletop'};
const base={message:'next',subgoal:'pick',remaining_steps:[]};
const state_id='a'.repeat(64),task_node='1:p2_pick';
test('generic goal and plan are independent of cube labels',()=>{
  const d=parseDecision(JSON.stringify({...base,decision:'observe',goal:{all:[{predicate:'in_region',object:'cylinder_a',region:'left'}]}}),context);
  assert.equal(d.goal.all[0].object,'cylinder_a');
  const p=parseDecision(JSON.stringify({...base,decision:'propose_plan',plan:{nodes:[{id:'pick',skill:{name:'acquire_object',arguments:{object:'block_b'}},depends_on:[]}]}}),context);
  assert.equal(p.plan.nodes.length,1);
});
test('generic prompt exposes registered providers without legacy recipes',()=>{
  const s=promptForContext(context);assert.match(s,/propose_plan/);assert.doesNotMatch(s,/three bounded visual CaP tasks/);
  assert.match(s,/independent Runtime check/);
});
test('generic parser rejects model-authored conditions and unbound actions',()=>{
  assert.throws(()=>parseDecision(JSON.stringify({...base,decision:'propose_plan',plan:{nodes:[{id:'a',skill:{name:'acquire_object',arguments:{}},requires:[]}]}}),context));
  assert.throws(()=>parseDecision(JSON.stringify({...base,decision:'run_code',code:'set_gripper("open")'}),context));
  assert.throws(()=>parseDecision(JSON.stringify({...base,decision:'observe',goal:{all:[{predicate:'teleport',object:'x'}]}}),context));
});
test('generic skill and code require dynamic node identities',()=>{
  assert.equal(parseDecision(JSON.stringify({...base,decision:'run_skill',state_id,task_node,skill:{name:'acquire_object',arguments:{object:'block_a'}}}),context).task_node,task_node);
  assert.equal(parseDecision(JSON.stringify({...base,decision:'run_code',task_ref:{state_id,task_node},code:'p=plan_grasp("block_a")'}),context).decision,'run_code');
});
test('mode budgets remain cumulative and preserve legacy defaults',()=>{
  const c=new RobotCoordinator({root:'/unused',configuration:()=>{},client:{}});
  c.task='cube_stack';assert.equal(c.wallBudgetMs(),300000);assert.equal(c.taskLimits().decisions,12);
  c.task='tabletop';assert.equal(c.wallBudgetMs(),600000);assert.deepEqual(c.taskLimits(),{decisions:24,calls:80,motion_steps:3000,wall_seconds:600});
});

import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const capRuntime=fileURLToPath(new URL('../../cap-runtime/',import.meta.url));
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {setTimeout as delay} from 'node:timers/promises';
import {RuntimeClient} from './runtime-client.mjs';
test('single S2 loop submits dynamic plan over HTTP and completes fixture without a task recipe',async()=>{
  const folder=await fs.mkdtemp(path.join(os.tmpdir(),'generic-system-'));
  const token='tabletop-integration-test-token';
  const program=`from agentic_cap.core import Manager
from agentic_cap.server import serve
from agentic_cap.tabletop_fixture import TabletopFixtureBackend
import sys,json
s=serve(Manager(sys.argv[1],TabletopFixtureBackend),'127.0.0.1',0,sys.argv[2])
print(json.dumps({'port':s.server_address[1]}),flush=True)
s.serve_forever()`;
  const child=spawn(process.env.ROBOT_TEST_PYTHON||'python',['-u','-c',program,path.join(folder,'runtime'),token],{cwd:capRuntime,windowsHide:true,env:{...process.env,PYTHONPATH:capRuntime},stdio:['ignore','pipe','pipe']});
  let failure;child.on('error',error=>failure=error);
  let output='',errors='';child.stdout.on('data',b=>output+=b);child.stderr.on('data',b=>errors+=b);
  let c; let rejected=0;
  try {
    for(let i=0;i<150&&!output.includes('\n');i++){if(failure||child.exitCode!==null)throw failure||new Error(errors);await delay(100);}
    assert.ok(output.includes('\n'),'Python fixture did not become ready within 15 seconds: '+errors);
    const client=new RuntimeClient('http://127.0.0.1:'+JSON.parse(output.split('\n')[0]).port,token);
    c=new RobotCoordinator({root:path.join(folder,'workbench'),client,configuration:async()=>({active:{}}),model:async(_channel,ctx)=>{
      const b={message:'test decision',remaining_steps:[]};
      if(ctx.mode==='interpretation')return {...b,decision:'observe',goal:{all:[{predicate:'on',object:'block_a',support:'platform'},{predicate:'empty_gripper'}]}};
      if(!ctx.robot_system.task.nodes.length && rejected++===0)throw Object.assign(new Error('Invalid flat decision schema'),{code:'INVALID_MODEL_DECISION'});
      if(!ctx.robot_system.task.nodes.length){assert.ok(ctx.latest_model_protocol_error);return {...b,decision:'propose_plan',plan:{nodes:[{id:'pick',skill:{name:'acquire_object',arguments:{object:'block_a'}}},{id:'place',skill:{name:'place_object',arguments:{object:'block_a',support:'platform'}},depends_on:['pick']}]}};}
      const n=ctx.robot_system.task.nodes.find(n=>n.status==='ready');assert.ok(n,'expected ready dynamic node');
      return {...b,decision:'run_skill',state_id:ctx.robot_system.state_id,task_node:n.id,skill:{name:n.skills[0],arguments:n.arguments}};
    }});
    await c.start({task:'tabletop',text:'put blue on platform',seed:201});await c.loopPromise;
    assert.equal(c.state.status,'completed',JSON.stringify(c.state.messages));
    assert.equal(c.state.episode.task,'tabletop');assert.equal(c.state.episode.limits.decisions,24);
    assert.equal(Object.keys(c.state.episode.segments).length,2);assert.equal(c.state.episode.used.decisions,5);
    assert.equal(c.state.verification.status,'verified');
    await c.close();
    c.model=async(_channel,ctx)=>{
      const b={message:'inspect or ask',remaining_steps:[]};
      if(ctx.mode==='interpretation')return {...b,decision:'observe',goal:{all:[{predicate:'holding',object:'block_a'}]}};
      if(!ctx.robot_system.task.nodes.length)return {...b,decision:'propose_plan',plan:{nodes:[{id:'pick',skill:{name:'acquire_object',arguments:{object:'block_a'}}}]}};
      return {...b,decision:'ask_user'};
    };
    await c.start({task:'tabletop',text:'hold blue',seed:201});await c.loopPromise;
    assert.equal(c.state.status,'awaiting_user');
    assert.equal(c.state.episode.assistance.hold_confirmed,true);
    const paused=c.state.episode;const observed=await client.request(`/episodes/${paused.id}/observe`);
    await assert.rejects(client.request(`/episodes/${paused.id}/skills`,{request_id:'foreign-after-assistance',revision:paused.revision,
      state_id:observed.system.state_id,task_node:observed.system.task.nodes[0].id,
      skill:{name:'acquire_object',arguments:{object:'block_a'}}}),{code:'AWAITING_USER'});
  } finally {if(c?.state.episode)await c.close().catch(()=>{});c?.eventsAbort?.abort();await c?.eventsPromise;await c?.recordQueue;if(child.exitCode===null&&child.signalCode===null){const closed=once(child,'close');child.kill();await closed;}await fs.rm(folder,{recursive:true,force:true});}
});

test('interpretation requires a root final goal and a text subgoal',()=>{
 assert.throws(()=>parseDecision(JSON.stringify({...base,decision:'observe',subgoal:{goal:{all:[{predicate:'holding',object:'block_a'}]}}}),{...context,mode:'interpretation'}));
 assert.throws(()=>parseDecision(JSON.stringify({...base,decision:'observe'}),{...context,mode:'interpretation'}));
});

test('an unchanged goal echo cannot replace Runtime authority, while a changed goal is rejected',()=>{
  const goal={all:[{predicate:'holding',object:'block_a'}]};
  const decision={...base,decision:'propose_plan',goal,plan:{nodes:[{id:'pick',skill:{name:'acquire_object',arguments:{object:'block_a'}}}]}};
  const ctx={...context,mode:'execution',goal};
  const accepted=parseDecision(JSON.stringify(decision),ctx);
  assert.equal(accepted.goal,undefined);
  assert.deepEqual(accepted.plan,decision.plan);
  assert.throws(()=>parseDecision(JSON.stringify({...decision,goal:{all:[{predicate:'holding',object:'block_b'}]}}),ctx));
});
