import fs from 'node:fs/promises';
import { setTimeout as delay } from 'node:timers/promises';
import { RuntimeClient } from './source/runtime/robot/runtime-client.mjs';
const options = JSON.parse(await fs.readFile(new URL('./options-diagnostic-resumed.json', import.meta.url), 'utf8'));
const token = (await fs.readFile(options.tokenFile, 'utf8')).trim();
const deadline = Date.now() + 180_000;
let ready = false;
while (Date.now() < deadline) {
  try {
    const rows = await Promise.all([18143, 18144].map(async port => ({ port,
      ...await new RuntimeClient(`http://127.0.0.1:${port}`, token).request('/health', undefined, undefined, 1500) })));
    if (rows.some(row => row.source_digest !== 'f089927499773a2027982873e725d96bfc410d7cd057052591683356cbbe481c'
      || row.features.support_camera_fault !== 'green_after_release_until_lateral_view'
      || !row.features.inspect_support || row.features.inspect_placement !== (row.port === 18144))) {
      throw Object.assign(new Error('Diagnostic provenance mismatch'), { fatal: true });
    }
    console.log(JSON.stringify(rows.map(row => ({ port: row.port, features: row.features, digest: row.source_digest }))));
    ready = true;
    break;
  } catch (error) { if (error.fatal) throw error; }
  await delay(1500);
}
if (!ready) throw new Error('Diagnostic servers not ready; no model task was dispatched');
