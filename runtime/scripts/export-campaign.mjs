// Download only terminal episode archives after evaluation, outside policy cost.
import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
import { RuntimeClient } from '../robot/runtime-client.mjs';
const [output, tokenFile, runtimeURL] = process.argv.slice(2);
const manifest = JSON.parse(await fs.readFile(path.join(output, 'campaign.json'), 'utf8'));
const client = new RuntimeClient(runtimeURL, (await fs.readFile(tokenFile, 'utf8')).trim());
const rows = JSON.parse(await fs.readFile(path.join(output, 'downloads.json'), 'utf8').catch(error => { if (error.code === 'ENOENT') return '[]'; throw error; }));
for (const trial of manifest.plan) {
  const folder = path.join(output, trial.trial_id);
  const row = JSON.parse(await fs.readFile(path.join(folder, 'result.json'), 'utf8').catch(error => { if (error.code === 'ENOENT') return 'null'; throw error; }));
  if (!row?.episode_id) continue;
  const previous = rows.find(r => r.trial_id === trial.trial_id);
  if (previous) {
    const bytes = await fs.readFile(path.join(folder, 'runtime.zip'));
    if (crypto.createHash('sha256').update(bytes).digest('hex') !== previous.sha256) throw new Error('Existing archive checksum mismatch');
    continue;
  }
  const episode = await client.request(`/episodes/${row.episode_id}`);
  if (!['closed', 'interrupted'].includes(episode.status)) throw new Error('Episode is not terminal: ' + row.episode_id);
  const start = Date.now();
  const bytes = await client.bytes(`/episodes/${row.episode_id}/export`, 600_000);
  await fs.writeFile(path.join(folder, 'runtime.zip'), bytes);
  rows.push({ trial_id: trial.trial_id, episode_id: row.episode_id, bytes: bytes.length,
    sha256: crypto.createHash('sha256').update(bytes).digest('hex'), seconds: (Date.now()-start)/1000 });
  await fs.writeFile(path.join(output, 'downloads.json'), JSON.stringify(rows, null, 2));
  console.log(JSON.stringify(rows.at(-1)));
}
