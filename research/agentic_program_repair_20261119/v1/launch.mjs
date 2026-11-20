import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { RuntimeClient } from './source/runtime/robot/runtime-client.mjs';
import { sourceManifest, assertRuntimeFeatures } from './source/runtime/scripts/robot-campaign.mjs';
const root=path.dirname(fileURLToPath(import.meta.url));
const names=['repair','normal'];
const options=names.map(n=>JSON.parse(fs.readFileSync(path.join(root,n+'-options.json'))));
for (const option of options) {
  if (fs.existsSync(path.join(option.output,'protocol.json')) || fs.existsSync(path.join(option.output,'campaign.json')))
    throw new Error('Suite already admitted; inspect original records, no replay');
}
const token=fs.readFileSync(options[0].tokenFile,'utf8').trim();
const sources=await sourceManifest(options[0].sourceRoot);
const digest=crypto.createHash('sha256').update(sources.files.filter(f=>f.path.startsWith('cap-runtime/'))
  .map(f=>f.path.slice('cap-runtime/'.length)+'\0'+f.sha256+'\n').join('')).digest('hex');
let healths;
const deadline=Date.now()+360_000;
while (Date.now()<deadline) {
  try {
    healths=await Promise.all(options.map(async (option,i)=>{
      const health=await new RuntimeClient(option.runtimeURL,token).request('/health',undefined,undefined,1500);
      if (health.source_digest!==digest) throw Object.assign(new Error('Frozen Runtime differs'),{fatal:true});
      try { assertRuntimeFeatures(health,option.expectedRuntimeFeatures); }
      catch(error) { throw Object.assign(error,{fatal:true}); }
      if (i===0 && (!health.diagnostic?.prepared || health.diagnostic.mode!=='field_access'))
        throw Object.assign(new Error('Preparation failed; preserve original case'),{fatal:true});
      return {url:option.runtimeURL,source_digest:health.source_digest,features:health.features,diagnostic:health.diagnostic?.prepared};
    }));
    break;
  } catch(error) { if (error.fatal) throw error; healths=null; }
  await delay(1000);
}
if (!healths) throw new Error('No model dispatch: Runtime unreachable');
fs.writeFileSync(path.join(root,'preflight.json'),JSON.stringify(healths,null,2));
for (const [i,option] of options.entries()) {
  const name=names[i];
  const child=spawn(path.resolve('desktop/node_modules/electron/dist/electron.exe'),
    [path.join(option.sourceRoot,'runtime/scripts/campaign-host.cjs'),path.join(root,name+'-options.json')],
    {windowsHide:true,stdio:['ignore',fs.openSync(path.join(root,name+'.stdout.log'),'w'),fs.openSync(path.join(root,name+'.stderr.log'),'w')]});
  fs.writeFileSync(path.join(root,name+'.pid'),String(child.pid));
  console.log(JSON.stringify({variant:name,pid:child.pid}));
  const code=await new Promise((resolve,reject)=>{child.on('error',reject);child.on('exit',resolve);});
  if (code!==0) throw new Error(name+' host exited '+code+'; inspect preserved admission, no replay');
}
fs.writeFileSync(path.join(root,'suite-complete.json'),JSON.stringify({hosts_exited_normally:true}));
