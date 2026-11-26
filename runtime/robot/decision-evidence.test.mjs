import test from 'node:test';
import assert from 'node:assert/strict';
import { acquireDecisionEvidence, newlyAvailableRoute } from './decision-evidence.mjs';
const frame = (status, index=1) => ({ evidence_ref:`frame-${index}`, age_seconds:.01,
  decision_evidence:{status}, system:{world:{facts:{goal:{status:'unknown'}}}} });
const options = values => ({ assertCurrent(){}, record:async()=>{}, milliseconds:100, ...values });

test('ready window with unknown facts returns immediately without waiting for success',async()=>{
  let reads=0;
  const result=await acquireDecisionEvidence(options({read:async()=>{reads++;return frame('ready');}}));
  assert.equal(reads,1);assert.equal(result.wait.status,'ready');
  assert.equal(result.observation.system.world.facts.goal.status,'unknown');
});

test('pending window is replaced by fresh ready evidence and emits both wait events',async()=>{
  let reads=0;const events=[];
  const result=await acquireDecisionEvidence(options({milliseconds:300,read:async()=>frame(++reads===1?'pending':'ready',reads),record:async(kind,value)=>events.push({kind,...value})}));
  assert.equal(reads,2);assert.equal(result.observation.evidence_ref,'frame-2');
  assert.deepEqual(events.map(e=>e.kind),['decision_evidence_wait_started','decision_evidence_wait_finished']);
  assert.equal(result.wait.status,'ready');
});

test('missing stream expires with pending evidence, never fabricates ready or success',async()=>{
  const result=await acquireDecisionEvidence(options({milliseconds:25,read:async()=>frame('pending')}));
  assert.equal(result.wait.status,'timed_out');assert.equal(result.observation.decision_evidence.status,'pending');
  assert.ok(result.wait.elapsed_milliseconds<300);
});

test('stalled follow-up request is bounded even when transport ignores abort',async()=>{
  let reads=0;
  const result=await acquireDecisionEvidence(options({milliseconds:150,read:async()=>++reads===1?frame('pending'):new Promise(()=>{})}));
  assert.equal(reads,2);assert.equal(result.wait.status,'timed_out');
  assert.equal(result.observation.evidence_ref,'frame-1');
});

test('stop interrupts a hung initial request without returning late evidence',async()=>{
  const controller=new AbortController();let resolve;
  const pending=acquireDecisionEvidence(options({signal:controller.signal,read:()=>new Promise(r=>resolve=r)}));
  await new Promise(r=>setTimeout(r,5));controller.abort();
  await assert.rejects(pending,{name:'AbortError'});resolve(frame('ready'));
});

test('goal epoch change during polling rejects the old observation before dispatch',async()=>{
  let epoch=1,reads=0;
  await assert.rejects(acquireDecisionEvidence(options({milliseconds:300,
    assertCurrent(){if(epoch!==1)throw new Error('Superseded');},
    read:async()=>{reads++;if(reads===2)epoch=2;return frame(reads===1?'pending':'ready');}})),/Superseded/);
});

test('unknown or unsupported readiness does not cause automatic repeated observations',async()=>{
  for(const observation of [frame('unknown'),{evidence_ref:'legacy'}]){
    let reads=0;const result=await acquireDecisionEvidence(options({read:async()=>{reads++;return observation;}}));
    assert.equal(reads,1);assert.ok(['unknown','unsupported'].includes(result.wait.status));
  }
});


test('assistance is reconsidered only for a newly ready route in the same goal and live episode',()=>{
  const before={system:{contract_version:1,episode_id:'e',goal_version:1,task:{execution:{ready_nodes:[]}}}};
  const after={system:{contract_version:1,episode_id:'e',goal_version:1,world:{fresh:true},
    executor:{status:'ready',active_segment:null,hold_pending:false},task:{execution:{ready_nodes:['1:acquire']}}}};
  assert.equal(newlyAvailableRoute(before,after),true);
  const variants=[s=>s.world.fresh=false,s=>s.goal_version=2,s=>s.episode_id='other',s=>s.executor.hold_pending=true,
    s=>s.executor.status='closed',s=>s.executor.active_segment='moving',s=>s.task.assistance={status:'awaiting_user'},
    s=>s.task.execution.ready_nodes=[]];
  for(const change of variants){const copy=structuredClone(after);change(copy.system);assert.equal(newlyAvailableRoute(before,copy),false);}
  const already=structuredClone(before);already.system.task.execution.ready_nodes=['1:acquire'];
  assert.equal(newlyAvailableRoute(already,after),false);
});
