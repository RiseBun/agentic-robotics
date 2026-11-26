// Receives configuration on stdin over the authenticated migration connection.
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { localCrypto } from '../../runtime/robot/local-crypto.mjs';
import { createConfigStore } from '../../runtime/config-store.mjs';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const data = process.env.EMBUDDIES_DATA_DIR || path.join(process.env.HOME,'.local/share/agentic-robotics/data');
globalThis.embuddiesCrypto = await localCrypto(data);
let raw = '';
for await (const part of process.stdin) { raw += part; if (raw.length > 100000) throw new Error('Import too large'); }
const value = JSON.parse(raw);
await createConfigStore(path.join(root,'runtime'),data).update({ activeChannel:value.channel,
  channelPatch:value.patch });
console.log(JSON.stringify({ imported_model:value.patch.model, channel:value.channel, key_stored:'encrypted on Linux' }));
