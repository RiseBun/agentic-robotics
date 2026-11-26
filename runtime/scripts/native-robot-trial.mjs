// Isolated, frozen real-model diagnostic on the Linux board. No production restart.
import fs from 'node:fs/promises';
import path from 'node:path';
import net from 'node:net';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { localCrypto } from '../robot/local-crypto.mjs';
import { createConfigStore } from '../config-store.mjs';
import { RuntimeClient } from '../robot/runtime-client.mjs';
import { runCampaign } from './robot-campaign.mjs';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const option=(name,fallback)=>{const i=process.argv.indexOf('--'+name);return i<0?fallback:process.argv[i+1];};
const output=path.resolve(option('output',''));
if (!option('output') || !output.startsWith(root+path.sep)) throw new Error('Use a new output directory inside this project');
const task=option('task','cube_restack'), seed=Number(option('seed','12')), port=Number(option('port','8121'));
const view=option('secondary-view','off');
const alternate=option('alternate-recovery','off');
if (!['off','on'].includes(alternate)) throw new Error('Invalid alternate recovery setting');
if (![8121,8122].includes(port) || !['off','wrist'].includes(view)) throw new Error('Invalid isolated runtime configuration');
await new Promise((resolve,reject)=>{const probe=net.createServer();probe.once('error',reject);probe.listen(port,'127.0.0.1',()=>probe.close(resolve));});
await fs.mkdir(output,{recursive:false});
const data=path.join(process.env.HOME,'.local/share/agentic-robotics/data');
globalThis.embuddiesCrypto=await localCrypto(data);
const configuration=createConfigStore(path.join(root,'runtime'),data);
const tokenFile=path.join(data,'runtime-token.txt'), token=(await fs.readFile(tokenFile,'utf8')).trim();
const log=await fs.open(path.join(output,'runtime.log'),'a');
const runtime=spawn(path.join(root,'.venv/bin/python'),['-u','-m','agentic_cap.server','--backend','cap','--host','127.0.0.1',
  '--port',String(port),'--upstream',path.join(root,'third_party/cap-x'),'--records',path.join(output,'physical'),'--evaluation'],
  {cwd:path.join(root,'cap-runtime'),stdio:['ignore',log.fd,log.fd],env:{...process.env,CAP_RUNTIME_TOKEN:token,
    CAP_PROJECT_ROOT:root,ROBOT_DESCRIPTIONS_CACHE:path.join(root,'cache/robot_descriptions'),MUJOCO_GL:'egl',PYOPENGL_PLATFORM:'egl',
    JAX_PLATFORMS:'cpu',CAP_ACTIVE_EVIDENCE:'1',CAP_PLACEMENT_EVIDENCE:'1',CAP_HOLDING_WATCHDOG:'1',
    CAP_SUPPORT_OCCLUSION:'',CAP_ALTERNATE_PLACEMENT:alternate==='on'?'1':'0',CAP_SUPPORT_SECOND_VIEW:view==='wrist'?'robot0_eye_in_hand':''}});
const exited=once(runtime,'exit');
const client=new RuntimeClient('http://127.0.0.1:'+port,token);
try {
  let health;
  for(let i=0;i<40;i++){
    if(runtime.exitCode!==null) throw new Error('Isolated runtime exited before ready');
    try {health=await client.request('/health');break;} catch {await delay(250);}
  }
  if(!health) throw new Error('Isolated runtime did not become ready');
  const result=await runCampaign({sourceRoot:root,output:path.join(output,'campaign'),runtimeURL:client.base,tokenFile,
    tasks:[task],seeds:[seed],maxTrials:1,settleMilliseconds:1500,
    expectedRuntimeFeatures:{alternate_placement:alternate==='on',support_second_view:view==='wrist'?'robot0_eye_in_hand':''}},()=>configuration.read());
  console.log(JSON.stringify(result));
} finally {
  if(runtime.exitCode===null) runtime.kill('SIGINT');
  let timer;
  const stopped=await Promise.race([exited.then(()=>true),new Promise(resolve=>{timer=setTimeout(()=>resolve(false),15000);})]);
  clearTimeout(timer);
  if(!stopped){runtime.kill('SIGKILL');await exited;}
  await log.close();
  if(!stopped) throw new Error('Runtime required forced shutdown; inspect preserved episodes');
}
