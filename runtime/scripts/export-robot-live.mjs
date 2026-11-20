// Engineering export check against the configured Runtime; no policy is run.
import { _electron } from '../node_modules/playwright/index.mjs';
import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const env={...process.env,EMBUDDIES_DATA_DIR:path.join(process.env.LOCALAPPDATA,'embuddies/data')};
delete env.ELECTRON_RUN_AS_NODE;
const packaged=process.env.ROBOT_EXPORT_EXE;
const app=await _electron.launch({executablePath:packaged || path.join(root,'desktop/node_modules/electron/dist/electron.exe'),
 args:packaged ? [] : [path.join(root,'desktop'),'--user-data-dir='+path.join(process.env.LOCALAPPDATA,'embuddies/profile')],env});
try {
 const page=await app.firstWindow();
 await page.getByRole('heading',{name:'告诉机器人要做什么'}).waitFor();
 for(let i=0;i<300;i++){
  if((await page.evaluate(()=>window.ROBOT_DESKTOP.runtimeStatus())).state==='ready')break;
  await new Promise(r=>setTimeout(r,100));
 }
 const started=Date.now();
 const settings=await page.evaluate(()=>window.ROBOT_DESKTOP.request({path:'/robot/settings',method:'GET'}));
 console.log(JSON.stringify({channel:settings.activeChannel,hasKey:settings.channels.find(c=>c.id===settings.activeChannel)?.hasKey,
  hasRuntimeToken:settings.hasRuntimeToken,packaged:Boolean(packaged)}));
 if(process.argv.includes('--reset-test-connection')) {
  await page.evaluate(()=>window.ROBOT_DESKTOP.request({path:'/robot/settings',method:'POST',
   body:{runtimeURL:'http://127.0.0.1:8120',runtimeToken:''}}));
  console.log(JSON.stringify({test_connection_cleared:true}));
 } else {
 const pack=await page.evaluate(()=>window.ROBOT_DESKTOP.request({path:'/robot/export',method:'GET'}));
 if(!pack.runtime_zip_base64 || !pack.workbench_jsonl)throw new Error('Incomplete task package');
 await fs.mkdir(path.dirname(path.resolve(process.argv[2])),{recursive:true});
 await fs.writeFile(process.argv[2],JSON.stringify(pack));
 console.log(JSON.stringify({task_id:pack.task_id,episode_id:pack.episode_id,
  runtime_bytes:Buffer.byteLength(pack.runtime_zip_base64,'base64'),elapsed_seconds:(Date.now()-started)/1000}));
 }
} finally {await app.close();}
