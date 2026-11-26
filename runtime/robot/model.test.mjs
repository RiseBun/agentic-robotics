import test from 'node:test';
import assert from 'node:assert/strict';
import { modelInput, parseDecision, requestDecision } from './model.mjs';

function fixture() {
  const segment = {id:'bad',execution:'failed',code:'p=get_object_pose("green cube")\nz=p[0][2]\ngoto_pose([p[0][0],p[0][1],nz],[0,0,1,0])',
    subgoal:'approach green',api_attempts:[],contract:{postcondition:{holding:'green cube'}}};
  return {mode:'execution',revision:4,goal:{on:['red cube','green cube'],empty_gripper:true},
    remaining_steps:['lift','clear','stack'],remaining_wall_milliseconds:20000,used_budget:{decisions:3},
    limits:{decisions:12},recent_attempts:[segment],latest_observation:{objects:{secretHistory:'not part of compiler repair'}},
    recovery_progress:{program_repair:{status:'revise_rejected_code',segment_id:'bad',rejected_code:segment.code,
      diagnostic:{kind:'unbound_name',name:'nz',available_locals:['p','z']}},
      recent_attempts:[{segment_id:'bad',execution:'failed',feedback:{category:'invalid_program'},api_attempts:[]}]}};
}
const channel={api:'openai-responses',baseURL:'https://example.invalid',model:'fixture',apiKey:'test-key'};
const output=decision=>({ok:true,json:async()=>({status:'completed',output:[{type:'message',role:'assistant',content:[{type:'output_text',text:JSON.stringify(decision)}]}]})});

test('preflight name repair excludes scene/history/images and preserves version/budget/diagnostic',()=>{
  const c=fixture(), input=modelInput(c,['data:image/png;base64,unused']);
  assert.equal(input.context.mode,'preflight_code_repair');
  assert.deepEqual(input.images,[]);
  assert.equal(input.context.revision,4);
  assert.deepEqual(input.context.used_budget,{decisions:3});
  assert.equal(input.context.rejected_code,c.recent_attempts[0].code);
  assert.equal(input.context.diagnostic.name,'nz');
  assert.equal(input.context.latest_observation,undefined);
  assert.equal(input.context.recent_attempts,undefined);
});

test('physical effects, missing proof, field errors and segment mismatch keep full model input',()=>{
  const cases=[c=>c.recent_attempts[0].api_attempts.push({api:'open_gripper'}),
    c=>c.recovery_progress.recent_attempts[0].api_attempts.push({api:'open_gripper'}),
    c=>delete c.recent_attempts[0].api_attempts,
    c=>c.recovery_progress.program_repair.diagnostic.kind='invalid_subscript',
    c=>c.recent_attempts[0].id='other',c=>c.recent_attempts[0].code='different',c=>c.mode='interpretation'];
  for(const mutate of cases){
    const c=fixture();mutate(c);const images=['image'];const input=modelInput(c,images);
    assert.equal(input.context,c);assert.equal(input.images,images);assert.equal(input.repair,null);
  }
});

test('repair uses ordinary transport, retains original phase/contract/plan and cannot redefine final goal',async()=>{
  const c=fixture();let sent;const records=[];
  const decision=await requestDecision(channel,c,['image'],new AbortController().signal,
    async(kind,value)=>records.push({kind,...value}),async(url,options)=>{
      sent=JSON.parse(options.body);
      return output({decision:'run_code',message:'fixed',subgoal:'new phase',remaining_steps:[],code:'z=1',
        goal:{holding:'red cube'},contract:{postcondition:{holding:'red cube'}}});
    });
  assert.equal(sent.input[0].content.length,1);
  assert.equal(JSON.parse(sent.input[0].content[0].text).mode,'preflight_code_repair');
  assert.equal(records[0].request_scope,'preflight_code_repair');
  assert.equal(decision.subgoal,'approach green');
  assert.deepEqual(decision.remaining_steps,c.remaining_steps);
  assert.deepEqual(decision.contract,c.recent_attempts[0].contract);
  assert.equal(decision.goal,undefined);
  assert.notEqual(decision.contract,c.recent_attempts[0].contract);
});

test('repair allows assistance but rejects observe/finish; abort signal reaches transport',async()=>{
  const c=fixture(),abort=new AbortController();
  const ask=await requestDecision(channel,c,[],abort.signal,async()=>{},async(url,options)=>{
    assert.equal(options.signal.aborted,false);
    return output({decision:'ask_user',message:'ambiguous',remaining_steps:[]});
  });
  assert.equal(ask.decision,'ask_user');
  for(const decision of ['observe','finish']) await assert.rejects(
    requestDecision(channel,c,[],abort.signal,async()=>{},async()=>output({decision,message:'done',remaining_steps:[]})),/代码修复/);
  abort.abort();
  await assert.rejects(requestDecision(channel,c,[],abort.signal,async()=>{},async(url,options)=>{
    options.signal.throwIfAborted();
  }),{name:'AbortError'});
});

test('bound code repair retains the Runtime node and rebinds its current state reference', async () => {
  const c = fixture();
  c.recent_attempts[0].task_binding = { node_id: '2:clear', goal_version: 2 };
  c.robot_system = { contract_version: 1, goal_version: 2, state_id: 'a'.repeat(64), task: { nodes: [{ id: '2:clear' }] } };
  const decision = await requestDecision(channel, c, [], new AbortController().signal, async () => {}, async () => output({
    decision: 'run_code', message: 'fixed', remaining_steps: [], code: 'z=1',
    task_ref: { state_id: 'b'.repeat(64), task_node: '2:place' }
  }));
  assert.deepEqual(decision.task_ref, { state_id: c.robot_system.state_id, task_node: '2:clear' });
  assert.deepEqual(decision.contract, c.recent_attempts[0].contract);
  c.robot_system.goal_version = 3;
  assert.equal(modelInput(c, []).repair, null);
});

test('code task references cannot include model-defined conditions or unsupported nodes', () => {
  const decision = { decision: 'run_code', message: 'bound', remaining_steps: [], code: 'pass',
    task_ref: { state_id: 'c'.repeat(64), task_node: '1:clear' } };
  assert.deepEqual(parseDecision(JSON.stringify(decision)).task_ref, decision.task_ref);
  for (const ref of [{ ...decision.task_ref, requirements: [] }, { ...decision.task_ref, task_node: '1:shell' },
    { ...decision.task_ref, state_id: 'old' }]) {
    assert.throws(() => parseDecision(JSON.stringify({ ...decision, task_ref: ref })));
  }
});


test('registered alternate reorientation node is accepted only through the skill decision protocol',()=>{
  const value={decision:'run_skill',message:'Try bounded high-clearance alternative',remaining_steps:[],
    state_id:'a'.repeat(64),task_node:'1:reorient',skill:{name:'reorient_held_object',arguments:{object:'red cube'}}};
  assert.equal(parseDecision(JSON.stringify(value)).task_node,'1:reorient');
  assert.throws(()=>parseDecision(JSON.stringify({...value,code:'open_gripper()'})));
  assert.throws(()=>parseDecision(JSON.stringify({...value,task_node:'1:unregistered'})));
  assert.throws(()=>parseDecision(JSON.stringify({decision:'run_code',message:'bypass',remaining_steps:[],code:'open_gripper()',
    task_ref:{state_id:value.state_id,task_node:'1:reorient'}})));
});
