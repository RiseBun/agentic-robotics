import test from 'node:test';
import assert from 'node:assert/strict';
import { boundedOperation } from './deadline.mjs';

test('deadline terminates a provider that ignores cancellation', async () => {
  let signal;
  const started = Date.now();
  await assert.rejects(boundedOperation(value => { signal = value; return new Promise(() => {}); },
    { milliseconds: 25, label: 'Model' }), error => error.code === 'OPERATION_TIMEOUT');
  assert.ok(Date.now()-started < 500);
  assert.equal(signal.aborted, true);
});

test('late values cannot resume an operation after its deadline', async () => {
  let resolve;
  const late = new Promise(done => { resolve = done; });
  await assert.rejects(boundedOperation(() => late, { milliseconds: 10 }));
  resolve('late code');
  await new Promise(done => setTimeout(done, 10));
});

test('stop aborts an uncooperative operation without waiting for its value', async () => {
  const stop = new AbortController();
  const result = boundedOperation(() => new Promise(() => {}), { signal: stop.signal, milliseconds: 5000 });
  stop.abort();
  await assert.rejects(result, error => error.name === 'AbortError');
});

test('already aborted calls never enter the provider', async () => {
  const stop = new AbortController();
  stop.abort();
  let called = false;
  await assert.rejects(boundedOperation(() => { called = true; }, { signal: stop.signal, milliseconds: 50 }));
  assert.equal(called, false);
});
