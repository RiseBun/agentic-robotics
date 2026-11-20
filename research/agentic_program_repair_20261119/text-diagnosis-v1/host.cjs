// Isolated model probes; no Runtime client or action routes.
const {app,safeStorage}=require('electron');
const fs=require('node:fs');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const root=__dirname;
const project=path.resolve(root,'../../..');
const scratch=path.join(project,'.tmp/contracts-campaign-private');
app.setPath('userData',path.join(scratch,'profile'));
app.setPath('sessionData',path.join(scratch,'profile/browser-profile'));
app.disableHardwareAcceleration();
app.whenReady().then(async()=>{
  globalThis.embuddiesCrypto=async(action,value)=>{
    if(action==='decrypt') return safeStorage.decryptString(Buffer.from(value,'base64'));
    throw new Error('Read-only diagnostic');
  };
  const load=relative=>import(pathToFileURL(path.join(project,relative)).href);
  const {createConfigStore}=await load('runtime/config-store.mjs');
  const {resolveModelTransport}=await load('runtime/model-providers/registry.mjs');
  const {responsesResult}=await load('runtime/model-providers/openai-responses.mjs');
  const {parseDecision}=await load('runtime/robot/model.mjs');
  const config=(await createConfigStore(path.join(project,'runtime'),path.join(scratch,'data')).read()).active;
  const expected=JSON.parse(fs.readFileSync(path.join(root,'../v2/repair-options.json'))).expectedModel;
  if(config.model!==expected.model || config.api!==expected.api || config.baseURL!==expected.baseURL || config.reasoningEffort!==expected.reasoningEffort) throw new Error('Model configuration differs');
  const transport=resolveModelTransport(config);
  fs.writeFileSync(path.join(root,'admission.json'),JSON.stringify({started_at:new Date().toISOString(),model:transport.model}),{flag:'wx'});
  for(const name of ['compact','full_text']){
    const body=JSON.parse(fs.readFileSync(path.join(root,name+'-request.json')));
    const start=Date.now();
    const response=await fetch(transport.endpoint,{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+transport.apiKey},body:JSON.stringify(body),signal:AbortSignal.timeout(90000)});
    if(!response.ok) throw new Error('Model HTTP '+response.status);
    const raw=await response.json();
    fs.writeFileSync(path.join(root,name+'-response.json'),JSON.stringify(raw,null,2));
    const decision=parseDecision(responsesResult(raw).text);
    fs.writeFileSync(path.join(root,name+'-result.json'),JSON.stringify({decision,elapsed_ms:Date.now()-start,usage:raw.usage},null,2));
    console.log(JSON.stringify({condition:name,decision:decision.decision,elapsed_ms:Date.now()-start}));
  }
  fs.writeFileSync(path.join(root,'complete.json'),JSON.stringify({finished_at:new Date().toISOString()}));
  app.exit(0);
}).catch(error=>{
  fs.writeFileSync(path.join(root,'error.json'),JSON.stringify({error:error.message}));
  console.error(error.message);app.exit(1);
});
