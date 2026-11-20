import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import { RuntimeClient } from './runtime-client.mjs';

test('Runtime timeout covers a response body that never finishes', { timeout: 1000 }, async () => {
  const client = new RuntimeClient('http://localhost:8120', 'private', async () => ({ ok: true, json: () => new Promise(() => {}) }));
  await assert.rejects(client.request('/episodes/e', undefined, undefined, 15), error => error.code === 'OPERATION_TIMEOUT');
});

test('Runtime cancellation ends even a fetcher that ignores the signal', { timeout: 1000 }, async () => {
  const client = new RuntimeClient('http://localhost:8120', 'private', async () => new Promise(() => {}));
  const stop = new AbortController();
  const pending = client.request('/episodes/e', undefined, stop.signal);
  stop.abort();
  await assert.rejects(pending, error => error.name === 'AbortError');
});

test('artifact body is bounded independently of successful HTTP headers', { timeout: 1000 }, async () => {
  const client = new RuntimeClient('http://localhost:8120', 'private', async () => ({ ok: true, arrayBuffer: () => new Promise(() => {}) }));
  await assert.rejects(client.bytes('/artifact', 15), error => error.code === 'OPERATION_TIMEOUT');
});

test('Runtime errors retain their admission identity under bounded requests', async () => {
  const client = new RuntimeClient('http://localhost:8120', 'private', async () => ({ ok: false, status: 409,
    json: async () => ({ error: 'Stale revision', code: 'STALE_REVISION' }) }));
  await assert.rejects(client.request('/episodes/e/segments', {}), error => error.code === 'STALE_REVISION');
});

async function hangingServer() {
  let received;
  const arrived = new Promise(resolve => { received = resolve; });
  const sockets = new Set();
  const server = http.createServer((_request, response) => {
    response.writeHead(200, { 'Content-Type': 'application/json' });
    response.write('{'); // Real successful headers and a body that never ends.
    received();
  });
  server.on('connection', socket => { sockets.add(socket); socket.on('close', () => sockets.delete(socket)); });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  return { arrived, url: `http://127.0.0.1:${server.address().port}`,
    close: async () => {
      for (const socket of sockets) socket.destroy();
      await new Promise(resolve => server.close(resolve));
    } };
}

test('native HTTP response stalls after headers and still obeys the Runtime deadline', { timeout: 3000 }, async () => {
  const server = await hangingServer();
  try {
    const client = new RuntimeClient(server.url, 'fixture-only');
    const pending = assert.rejects(client.request('/episodes/e', undefined, undefined, 250),
      error => error.code === 'OPERATION_TIMEOUT');
    await server.arrived;
    await pending;
  } finally { await server.close(); }
});

test('native HTTP body read is interrupted by stop without waiting for server completion', { timeout: 3000 }, async () => {
  const server = await hangingServer();
  try {
    const client = new RuntimeClient(server.url, 'fixture-only');
    const stop = new AbortController();
    const pending = assert.rejects(client.request('/episodes/e', undefined, stop.signal), error => error.name === 'AbortError');
    await server.arrived;
    stop.abort();
    await pending;
  } finally { await server.close(); }
});
