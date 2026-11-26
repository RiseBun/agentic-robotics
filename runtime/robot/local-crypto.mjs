import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';

// A private, random per-install key for the Linux host; never derive it from paths.
export async function localCrypto(dataRoot) {
  await fs.mkdir(dataRoot, { recursive: true, mode: 0o700 });
  const file = path.join(dataRoot, 'host-crypto.key');
  try { await fs.writeFile(file, crypto.randomBytes(32), { flag: 'wx', mode: 0o600 }); }
  catch (error) { if (error.code !== 'EEXIST') throw error; }
  const key = await fs.readFile(file);
  if (key.length !== 32) throw new Error('Invalid host encryption key');
  if (process.platform !== 'win32') await fs.chmod(file, 0o600);
  return async (action, value) => {
    if (action === 'encrypt') {
      const iv = crypto.randomBytes(12), cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
      const body = Buffer.concat([cipher.update(value, 'utf8'), cipher.final()]);
      return Buffer.concat([iv, cipher.getAuthTag(), body]).toString('base64');
    }
    if (action !== 'decrypt') throw new Error('Unsupported crypto operation');
    const raw = Buffer.from(value, 'base64');
    const cipher = crypto.createDecipheriv('aes-256-gcm', key, raw.subarray(0, 12));
    cipher.setAuthTag(raw.subarray(12, 28));
    return Buffer.concat([cipher.update(raw.subarray(28)), cipher.final()]).toString('utf8');
  };
}
