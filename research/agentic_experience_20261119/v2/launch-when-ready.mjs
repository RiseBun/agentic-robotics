import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { spawn } from 'node:child_process';
import { setTimeout as delay } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { RuntimeClient } from './source/runtime/robot/runtime-client.mjs';
import { sourceManifest } from './source/runtime/scripts/robot-campaign.mjs';
const root = path.dirname(fileURLToPath(import.meta.url));
const options = JSON.parse(fs.readFileSync(path.join(root, 'options.json'), 'utf8'));
const token = fs.readFileSync(options.tokenFile, 'utf8').trim();
const source = await sourceManifest(options.sourceRoot);
const digest = crypto.createHash('sha256').update(source.files.filter(f => f.path.startsWith('cap-runtime/'))
  .map(f => f.path.slice('cap-runtime/'.length)+'\0'+f.sha256+'\n').join('')).digest('hex');
const deadline = Date.now()+240_000;
let healths;
while (Date.now() < deadline) {
  try {
    const next = await Promise.all(options.retreatEpisodes.map(async entry => {
      const health = await new RuntimeClient(entry.url, token).request('/health', undefined, undefined, 1500);
      if (health.source_digest !== digest || health.diagnostic?.mode !== entry.fault_mode || !health.diagnostic?.prepared
          || health.diagnostic.task !== 'cube_restack' || health.diagnostic.seed !== 66) {
        throw Object.assign(new Error('Frozen preparation failed: '+entry.mode+' '+(health.diagnostic?.error || 'provenance mismatch')), { fatal: true });
      }
      return { mode: entry.mode, episode_id: health.diagnostic.episode_id, source_digest: health.source_digest };
    }));
    healths = next; break;
  } catch (error) { if (error.fatal) throw error; }
  await delay(1000);
}
if (!healths) throw new Error('Prepared episodes unreachable; no model dispatched');
fs.writeFileSync(path.join(root, 'preflight.json'), JSON.stringify(healths, null, 2));
const exe = path.resolve('desktop/node_modules/electron/dist/electron.exe');
const child = spawn(exe, [path.join(options.sourceRoot, 'runtime/scripts/campaign-host.cjs'), path.join(root, 'options.json')],
  { windowsHide: true, stdio: ['ignore', fs.openSync(path.join(root, 'model.stdout.log'), 'w'), fs.openSync(path.join(root, 'model.stderr.log'), 'w')] });
fs.writeFileSync(path.join(root, 'model.pid'), String(child.pid));
console.log(JSON.stringify({ model_host_pid: child.pid, prepared: healths }));
const code = await new Promise((resolve, reject) => { child.on('error', reject); child.on('exit', resolve); });
console.log(JSON.stringify({ model_host_exit: code }));
process.exitCode = code || 0;
