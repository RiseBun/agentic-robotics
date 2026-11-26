import test from 'node:test';
import assert from 'node:assert/strict';
import { sceneImagePlan, loadSceneImages } from './scene-images.mjs';
import { RuntimeClient } from './runtime-client.mjs';

function bundle() {
  const root={episode_id:'episode',control_step:120,captured_at:100,age_seconds:.02,
    image_ref:'frame-000120.png',evidence_ref:'frame-000120.npz',coordinate_convention:{frame:'cap_robot_base'},camera_views:{}};
  for (const name of ['frontview','agentview','robot0_eye_in_hand']) root.camera_views[name]={
    ...root,camera_views:undefined,camera_name:name,image_ref:`view-${name}-000120.png`,evidence_ref:`view-${name}-000120.npz`};
  return root;
}

test('all registered coherent camera views retain explicit source and bounded ordering',()=>{
  const plan=sceneImagePlan(bundle(),'episode');
  assert.deepEqual(plan.sources.map(s=>s.camera_name),['robot0_robotview','robot0_eye_in_hand','frontview','agentview']);
  assert.equal(plan.rejected.length,0);
});

test('stale, miscalibrated, mismatched and unrecorded secondary views never trigger artifact reads',()=>{
  const changes=[v=>v.control_step--,v=>v.episode_id='old',v=>v.captured_at+=.2,
    v=>v.coordinate_convention={frame:'world'},v=>v.image_ref='../../secret.png',
    v=>v.evidence_ref='view-frontview-000119.npz',v=>v.captured_at=NaN];
  for(const change of changes){
    const root=bundle();change(root.camera_views.frontview);
    const plan=sceneImagePlan(root,'episode');
    assert.ok(!plan.sources.some(s=>s.camera_name==='frontview'));
    assert.ok(plan.rejected.some(s=>s.camera_name==='frontview'));
  }
  const root=bundle();root.camera_views.secret=root.camera_views.frontview;
  assert.equal(sceneImagePlan(root,'episode').rejected.at(0).reason,'unregistered_camera');
  root.age_seconds=2;assert.equal(sceneImagePlan(root,'episode').sources.length,0);
});

test('missing optional artifact degrades explicitly without losing other views or misnumbering images',async()=>{
  const client={bytes:async route=>{if(route.includes('frontview'))throw Error('missing');return Buffer.from('image');}};
  const result=await loadSceneImages(sceneImagePlan(bundle(),'episode'),client,'episode',null,()=>{});
  assert.equal(result.images.length,3);
  assert.deepEqual(result.sources.map(s=>s.input_index),[0,1,2]);
  assert.equal(result.sources.at(-1).camera_name,'agentview');
  assert.equal(result.rejected[0].reason,'artifact_unavailable');
});

test('stop aborts a native artifact body stalled after successful headers',async()=>{
  let started;
  const ready=new Promise(resolve=>started=resolve);
  const client=new RuntimeClient('http://localhost:8120','test',async()=>{
    started();return new Response(new ReadableStream({start(){}}));
  });
  const controller=new AbortController();
  const pending=loadSceneImages(sceneImagePlan(bundle(),'episode'),client,'episode',controller.signal,()=>controller.signal.throwIfAborted());
  await ready;controller.abort(new Error('user stop'));
  await assert.rejects(pending,/user stop/);
});

test('changed goal during late artifact response prevents any late image use',async()=>{
  let resolve,requested;
  const ready=new Promise(r=>requested=r);let current=true;
  const client={bytes:async()=>{requested();return new Promise(r=>resolve=r);}};
  const pending=loadSceneImages(sceneImagePlan(bundle(),'episode'),client,'episode',null,()=>{if(!current)throw Error('superseded');});
  await ready;current=false;resolve(Buffer.from('late'));
  await assert.rejects(pending,/superseded/);
});
