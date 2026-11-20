import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { RuntimeClient } from './source/runtime/robot/runtime-client.mjs';
import { sourceManifest, assertRuntimeFeatures } from './source/runtime/scripts/robot-campaign.mjs';
const root = path.dirname(fileURLToPath(import.meta.url));
const options = JSON.parse(fs.readFileSync(path.join(root, 'options.json'), 'utf8'));
if (fs.existsSync(path.join(options.output, 'paired-campaign.json'))) throw new Error('Already admitted; inspect original task identities instead of replay');
const token = fs.readFileSync(options.tokenFile, 'utf8').trim();
const sources = await sourceManifest(options.sourceRoot);
const digest = crypto.createHash('sha256').update(sources.files.filter(f => f.path.startsWith('cap-runtime/'))
  .map(f => f.path.slice('cap-runtime/'.length)+'\0'+f.sha256+'\n').join('')).digest('hex');
let healths;
const deadline = Date.now()+360_000;
while (Date.now() < deadline) {
  try {
    healths = await Promise.all(Object.entries(options.runtimeURLs).map(async ([variant, url]) => {
      const health = await new RuntimeClient(url, token).request('/health', undefined, undefined, 1500);
      if (health.source_digest !== digest) throw Object.assign(new Error('Runtime source mismatch'), { fatal: true });
      try { assertRuntimeFeatures(health, options.runtimeFeatures[variant]); }
      catch (error) { throw Object.assign(error, { fatal: true }); }
      return { variant, source_digest: health.source_digest, features: health.features };
    }));
    break;
  } catch (error) { if (error.fatal) throw error; healths = undefined; }
  await delay(1000);
}
if (!healths) throw new Error('Runtime unreachable; no model dispatched');
fs.writeFileSync(path.join(root, 'preflight.json'), JSON.stringify(healths, null, 2));
const child = spawn(path.resolve('desktop/node_modules/electron/dist/electron.exe'),
  [path.join(options.sourceRoot, 'runtime/scripts/campaign-host.cjs'), path.join(root, 'options.json')],
  { windowsHide: true, stdio: ['ignore', fs.openSync(path.join(root, 'model.stdout.log'), 'w'), fs.openSync(path.join(root, 'model.stderr.log'), 'w')] });
fs.writeFileSync(path.join(root, 'model.pid'), String(child.pid));
console.log(JSON.stringify({ model_host_pid: child.pid, healths }));
const code = await new Promise((resolve, reject) => { child.on('error', reject); child.on('exit', resolve); });
console.log(JSON.stringify({ model_host_exit: code }));
process.exitCode = code || 0;
