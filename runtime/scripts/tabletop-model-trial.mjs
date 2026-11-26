// Isolated development runs with the existing configured model. No secrets logged.
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import net from 'node:net';
import {spawn,execFileSync} from 'node:child_process';
import {once} from 'node:events';
import {setTimeout as delay} from 'node:timers/promises';
import {fileURLToPath} from 'node:url';
import {localCrypto} from '../robot/local-crypto.mjs';
import {createConfigStore} from '../config-store.mjs';
import {RuntimeClient} from '../robot/runtime-client.mjs';
import {RobotCoordinator} from '../robot/coordinator.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const option=(name,fallback)=>{const i=process.argv.indexOf('--'+name);return i<0?fallback:process.argv[i+1];};
const output=path.resolve(option('output',''));
if(!option('output')||!output.startsWith(root+path.sep))throw new Error('Use a fresh output directory inside the Thor project');
const seed=Number(option('seed','201')),task=option('task','hold');
const condition=option('condition','system_no_memory');
if(!['direct_cap','system_no_memory','system_with_memory'].includes(condition))throw new Error('Invalid comparison condition');
const inspection=option('inspection','lateral');
if(!['lateral','coverage','evidence'].includes(inspection))throw new Error('Invalid inspection policy');
const perception=option('perception','single_view');
if(!['single_view','multiview'].includes(perception))throw new Error('Invalid perception mode');
const campaignFile=option('campaign');
let campaign=null;
if(campaignFile){
  campaign=JSON.parse(await fs.readFile(path.resolve(campaignFile),'utf8'));
  const gates=JSON.parse(await fs.readFile(path.join(path.dirname(path.resolve(campaignFile)),'gates.json'),'utf8'));
  if(['A','B','C','D_validation'].some(k=>gates[k]?.status!=='passed'))throw new Error('Frozen campaign gates are incomplete');
  if(![701,702,703].includes(seed)||!campaign.runs.some(r=>r.seed===seed&&r.task===task&&r.condition===condition))throw new Error('Run is outside the frozen campaign');
}else if(![201,202].includes(seed))throw new Error('This command is development-only; validation/final seeds require a frozen campaign');
const library=option('library','');
if(condition==='system_with_memory'&&!library)throw new Error('Memory condition requires a frozen verified library');

const cases=JSON.parse(execFileSync(path.join(root,'.venv/bin/python'),['-c',
  'import json; from agentic_cap.tabletop_campaign import TASKS; print(json.dumps(TASKS))'],
  {cwd:path.join(root,'cap-runtime'),encoding:'utf8'}));
const taskCase=cases.find(c=>c.id===task);
if(!taskCase)throw new Error('Unsupported development case');
if(campaign){
  const frozen=campaign.tasks?.find(c=>c.id===task);
  if(JSON.stringify(frozen)!==JSON.stringify(taskCase))throw new Error('Frozen task input or expected goal changed');
  execFileSync(path.join(root,'.venv/bin/python'),['-c',
    'import sys,json; from agentic_cap.tabletop_campaign import admit_campaign; m=json.load(open(sys.argv[1])); g=json.load(open(sys.argv[2])); admit_campaign(g,m,m["library_fingerprint"])',
    path.resolve(campaignFile),path.join(path.dirname(path.resolve(campaignFile)),'gates.json')],
    {cwd:path.join(root,'cap-runtime'),encoding:'utf8'});
}
const port=8121;
await new Promise((resolve,reject)=>{const s=net.createServer();s.once('error',reject);s.listen(port,'127.0.0.1',()=>s.close(resolve));});
await fs.mkdir(output,{recursive:false});
const data=path.join(process.env.HOME,'.local/share/agentic-robotics/data');
globalThis.embuddiesCrypto=await localCrypto(data);
const config=createConfigStore(path.join(root,'runtime'),data);
const fixedConfiguration=await config.read();
const modelFingerprint=crypto.createHash('sha256').update(JSON.stringify(fixedConfiguration.active)).digest('hex');
const s2Files=['coordinator.mjs','model.mjs','tabletop-model.mjs','scene-images.mjs','runtime-client.mjs'];
const s2Fingerprint=crypto.createHash('sha256');
for(const name of s2Files)s2Fingerprint.update(name+'\0'+await fs.readFile(path.join(root,'runtime/robot',name)));
const s2Digest=s2Fingerprint.digest('hex');
if(campaign&&campaign.model_fingerprint!==modelFingerprint)throw new Error('Frozen model configuration changed');
const token=crypto.randomBytes(32).toString('hex');const log=await fs.open(path.join(output,'runtime.log'),'a');
const runtime=spawn(path.join(root,'.venv/bin/python'),['-u','-m','agentic_cap.server','--backend','cap','--host','127.0.0.1','--port',String(port),'--upstream',path.join(root,'third_party/cap-x'),'--records',path.join(output,'physical'),'--evaluation'],
 {cwd:path.join(root,'cap-runtime'),stdio:['ignore',log.fd,log.fd],env:{...process.env,...(library?{TABLETOP_SKILL_LIBRARY:path.resolve(library)}:{}),CAP_RUNTIME_TOKEN:token,TABLETOP_INSPECTION_POLICY:inspection,TABLETOP_MULTIVIEW:perception==='multiview'?'1':'0',MUJOCO_GL:'egl',PYOPENGL_PLATFORM:'egl',JAX_PLATFORMS:'cpu'}});
const exited=once(runtime,'exit');const client=new RuntimeClient('http://127.0.0.1:'+port,token);
const coordinator=new RobotCoordinator({root:path.join(output,'workbench'),configuration:async()=>fixedConfiguration,client});
try {
 for(let i=0;i<120;i++){try{await client.request('/health');break;}catch{if(runtime.exitCode!==null)throw new Error('Runtime startup failed');await delay(250);}}
 const configuration=fixedConfiguration;
 const runtimeHealth=await client.request('/health');
 const runtimeFingerprint=crypto.createHash('sha256').update(runtimeHealth.source_digest+'\0'+s2Digest+'\0'+perception+'\0'+inspection).digest('hex');
 if(campaign&&campaign.runtime_fingerprint!==runtimeFingerprint)throw new Error('Frozen Runtime or S2 source changed');
 const retrievedLibrary=library?await import('node:child_process').then(({execFileSync})=>JSON.parse(execFileSync(path.join(root,'.venv/bin/python'),['-c','import sys,json; from agentic_cap.tabletop_memory import SkillLibrary; print(json.dumps(SkillLibrary(sys.argv[1]).fingerprint()))',path.resolve(library)],{cwd:path.join(root,'cap-runtime'),encoding:'utf8'}))):null;
 if(campaign&&campaign.library_fingerprint!==retrievedLibrary&&condition==='system_with_memory')throw new Error('Frozen library changed');
 coordinator.tabletopExperienceEnabled=condition==='system_with_memory';
 await fs.writeFile(path.join(output,'protocol.json'),JSON.stringify({stage:campaign?'frozen_test':'development',task,seed,started_at:new Date().toISOString(),scene:taskCase.scene,model:configuration.active?.model,configuration_fingerprint:modelFingerprint,runtime_fingerprint:runtimeFingerprint,runtime:runtimeHealth,condition,perception,inspection,library_fingerprint:retrievedLibrary,memory:condition==='system_with_memory',input_text:taskCase.text,expected_goal:taskCase.goal,scope:'real model; no task-specific program supplied'},null,2));
 await coordinator.start({task:'tabletop',text:taskCase.text,scene:taskCase.scene,seed,policy:condition==='direct_cap'?'direct_cap':'system'});
 await coordinator.loopPromise;
 if (coordinator.state.episode) {
   const registeredGoal=JSON.stringify(coordinator.state.episode.goal);
   const wasCompleted=coordinator.state.status==='completed';
   const remaining=(coordinator.state.episode.limits.wall_seconds-coordinator.state.episode.used.wall_seconds)*1000;
   await delay(Math.max(0,Math.min(1500,remaining-250)));
   await coordinator.refresh();
   if(JSON.stringify(coordinator.state.episode.goal)!==registeredGoal)throw new Error('Goal changed during final acceptance; invalid experiment');
   if(wasCompleted&&!await coordinator.verifyGoal(coordinator.epoch,coordinator.state.episode.revision,'settled_final_acceptance')) {
     coordinator.state.status='needs_attention';
     coordinator.state.messages.push({role:'system',text:'Final settled effect is no longer publicly verified; completion is withheld.'});
   }
 }
 const currentPython=JSON.parse((await import('node:child_process')).execFileSync(path.join(root,'.venv/bin/python'),
   ['-c','import json; from agentic_cap.server import runtime_provenance; print(json.dumps(runtime_provenance("cap")))'],
   {cwd:path.join(root,'cap-runtime'),encoding:'utf8'}));
 const finalS2=crypto.createHash('sha256');
 for(const name of s2Files)finalS2.update(name+'\0'+await fs.readFile(path.join(root,'runtime/robot',name)));
 if(currentPython.source_digest!==runtimeHealth.source_digest||finalS2.digest('hex')!==s2Digest)throw new Error('Source changed during this run; records retained but result is not a frozen evaluation');
 await fs.writeFile(path.join(output,'result.json'),JSON.stringify({status:coordinator.state.status,task_wall_seconds:(Date.now()-coordinator.taskStartedAt)/1000,verification:coordinator.state.verification,episode:coordinator.state.episode,messages:coordinator.state.messages},null,2));
 console.log(JSON.stringify({output,status:coordinator.state.status,verification:coordinator.state.verification?.status,used:coordinator.state.episode?.used}));
} finally {
 if(coordinator.state.episode)await coordinator.close().catch(()=>{});
 coordinator.eventsAbort?.abort();runtime.kill('SIGINT');await Promise.race([exited,delay(10000)]);
 if(runtime.exitCode===null)runtime.kill('SIGKILL');await log.close();
}
