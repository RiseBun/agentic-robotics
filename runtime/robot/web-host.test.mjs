import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { once } from 'node:events';
import http from 'node:http';
import { createWebHost } from './web-host.mjs';
import { localCrypto } from './local-crypto.mjs';

test('Linux host uses a persistent random key and rejects ciphertext tampering', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'robot-host-'));
  try {
    const a = await localCrypto(root), value = await a('encrypt', 'test-secret');
    assert.equal(await (await localCrypto(root))('decrypt', value), 'test-secret');
    const raw = Buffer.from(value, 'base64'); raw[raw.length-1] ^= 1;
    await assert.rejects(a('decrypt', raw.toString('base64')));
    assert.equal((await fs.readFile(path.join(root, 'host-crypto.key'))).length, 32);
    if (process.platform !== 'win32') assert.equal((await fs.stat(path.join(root, 'host-crypto.key'))).mode & 0o777, 0o600);
  } finally { await fs.rm(root, { recursive: true, force: true }); }
});

test('browser host admits only local same-origin robot routes and bounded assets', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'robot-web-'));
  const calls = [];
  await fs.writeFile(path.join(root, 'index.html'), '<h1>Robot</h1>');
  const server = createWebHost({ uiRoot: root, invoke: async value => { calls.push(value); return { ok: true }; } });
  server.listen(0, '127.0.0.1'); await once(server, 'listening');
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    assert.match(await (await fetch(base)).text(), /Robot/);
    const head = await fetch(base, { method: 'HEAD' });
    assert.equal(head.status, 200);
    assert.equal(await head.text(), '');
    assert.equal((await fetch(base+'/api/robot/state')).status, 200);
    assert.equal((await fetch(base+'/api/robot/stop', { method:'POST', headers:{'Content-Type':'application/json', Origin:base}, body:'{}' })).status, 200);
    const count = calls.length;
    assert.equal((await fetch(base+'/api/robot/stop', { method:'POST', headers:{'Content-Type':'application/json', Origin:'https://foreign.test'}, body:'{}' })).status, 403);
    const foreignHostStatus = await new Promise((resolve, reject) => {
      const request = http.get(base+'/api/robot/state', { headers:{Host:'foreign.test'} }, response => {
        response.resume(); resolve(response.statusCode);
      });
      request.on('error', reject);
    });
    assert.equal(foreignHostStatus, 403);
    assert.equal((await fetch(base+'/api/terminal')).status, 404);
    assert.equal((await fetch(base+'/api/robot/stop', { method:'POST', body:'{}' })).status, 415);
    assert.equal((await fetch(base+'/%2e%2e%2fsecret.key')).status, 403);
    assert.equal(calls.length, count);
  } finally { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); await fs.rm(root,{recursive:true,force:true}); }
});
