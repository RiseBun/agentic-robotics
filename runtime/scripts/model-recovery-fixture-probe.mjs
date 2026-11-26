// Real configured model, synthetic authenticated Runtime: not a physical score.
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {setTimeout as delay} from 'node:timers/promises';
import {fileURLToPath} from 'node:url';
import {localCrypto} from '../robot/local-crypto.mjs';
import {createConfigStore} from '../config-store.mjs';
import {RuntimeClient} from '../robot/runtime-client.mjs';
import {RobotCoordinator} from '../robot/coordinator.mjs';
const root=fileURLToPath(new URL('../../',import.meta.url));
const output=path.resolve(process.argv[2]||'');
if(!process.argv[2]||!output.startsWith(root))throw new Error('Fresh output inside project required');
await fs.mkdir(output,{recursive:false});
const data=path.join(process.env.HOME,'.local/share/agentic-robotics/data');
globalThis.embuddiesCrypto=await localCrypto(data);
const configuration=await createConfigStore(path.join(root,'runtime'),data).read();
const fingerprint=crypto.createHash('sha256').update(JSON.stringify(configuration.active)).digest('hex');
const files=['coordinator.mjs','model.mjs','tabletop-model.mjs','runtime-client.mjs'];
const source=crypto.createHash('sha256');
for(const file of files)source.update(file+'\0'+await fs.readFile(path.join(root,'runtime/robot',file)));
await fs.writeFile(path.join(output,'protocol.json'),JSON.stringify({fixture_only:true,model:configuration.active?.model,model_fingerprint:fingerprint,s2_fingerprint:source.digest('hex'),modes:['recoverable','persistent'],trials_per_mode:1,goal:'place blue block on grey platform and leave gripper empty',budgets:{decisions:24,calls:80,motion_steps:3000,wall_seconds:600},scope:'real model decision behavior over synthetic public evidence; not robot performance or frozen benchmark'},null,2));
const results=[];
for(const mode of ['recoverable','persistent']){
 const folder=path.join(output,mode);await fs.mkdir(folder);
 const token=crypto.randomBytes(32).toString('hex');
 const child=spawn(path.join(root,'.venv/bin/python'),['-u','-m','experiments.model_recovery_fixture','--records',path.join(folder,'records'),'--mode',mode],{cwd:path.join(root,'cap-runtime'),env:{...process.env,CAP_RUNTIME_TOKEN:token},stdio:['ignore','pipe','pipe']});
 let out='',errors='',failure;child.on('error',e=>failure=e);child.stdout.on('data',b=>out+=b);child.stderr.on('data',b=>errors+=b);
 let c;
 try{
  for(let i=0;i<150&&!out.includes('\n');i++){if(failure||child.exitCode!==null)throw failure||new Error(errors);await delay(100);}
  if(!out.includes('\n'))throw new Error('Fixture startup timeout');
  const client=new RuntimeClient('http://127.0.0.1:'+JSON.parse(out.split('\n')[0]).port,token);
  c=new RobotCoordinator({root:path.join(folder,'workbench'),client,configuration:async()=>configuration});c.tabletopExperienceEnabled=false;
  await c.start({task:'tabletop',text:'Place the blue block on the grey platform and leave the gripper empty.',seed:201});await c.loopPromise;
  const result={mode,fixture_only:true,status:c.state.status,verification:c.state.verification,messages:c.state.messages,used:c.state.episode?.used};
  await c.close();
  await fs.writeFile(path.join(folder,'result.json'),JSON.stringify(result,null,2));
  results.push(result);await fs.writeFile(path.join(output,'results.json'),JSON.stringify(results,null,2));
  console.log(JSON.stringify({mode,status:result.status,verification:result.verification.status,used:result.used}));
 }finally{
  if(c?.state.episode&&c.state.status!=='closed')await c.close().catch(()=>{});
  if(child.exitCode===null&&child.signalCode===null){const closed=once(child,'close');child.kill('SIGINT');await Promise.race([closed,delay(3000)]);if(child.exitCode===null&&child.signalCode===null)child.kill('SIGKILL');}
 }
}
