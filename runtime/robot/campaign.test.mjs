import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { trialPlan, sourceManifest, pairedPlan, assertRuntimeFeatures } from '../scripts/robot-campaign.mjs';

test('campaign rejects wrong or missing sensor features before admitting an episode', () => {
  const expected = { support_second_view: 'robot0_eye_in_hand', support_camera_fault: '' };
  assert.doesNotThrow(() => assertRuntimeFeatures({ features: expected }, expected));
  assert.throws(() => assertRuntimeFeatures({ features: { ...expected, support_second_view: '' } }, expected));
  assert.throws(() => assertRuntimeFeatures({ features: { support_second_view: 'robot0_eye_in_hand' } }, expected));
  assert.throws(() => assertRuntimeFeatures({}, expected));
});

test('paired plans reject duplicate identities, invalid tasks and unknown variants', () => {
  const variants = { baseline: 'before', candidate: 'after' };
  const row = { pair_id: 'restack-s41', variant: 'baseline', task: 'cube_restack', seed: 41 };
  assert.equal(pairedPlan([row, { ...row, variant: 'candidate' }], variants).length, 2);
  for (const schedule of [[row], [row, row], [row, { ...row, variant: 'candidate', seed: 42 }],
    [{ ...row, variant: 'unknown' }], [{ ...row, pair_id: '../escape' }], [{ ...row, task: 'unregistered' }]]) {
    assert.throws(() => pairedPlan(schedule, variants));
  }
});

test('campaign plans retain every seed/task/repeat rather than selecting successes', () => {
  const plan = trialPlan(['cube_lifting', 'cube_stack', 'cube_restack'], [0, 11, 12], 2);
  assert.equal(plan.length, 18);
  assert.equal(new Set(plan.map(r => r.trial_id)).size, 18);
  assert.deepEqual(plan.slice(0, 6).map(r => r.seed), [0, 0, 0, 0, 0, 0]);
  assert.throws(() => trialPlan(['cube_lifting'], [0, 0]));
  assert.throws(() => trialPlan(['unregistered'], [0]));
  assert.throws(() => trialPlan(['cube_lifting'], [-1]));
});

test('source provenance detects runtime or model changes even without a Git commit', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'robot-source-'));
  try {
    for (const folder of ['runtime/robot', 'runtime/model-providers', 'cap-runtime/agentic_cap']) await fs.mkdir(path.join(root, folder), { recursive: true });
    const file = path.join(root, 'runtime/robot/model.mjs');
    await fs.writeFile(file, 'baseline');
    const before = await sourceManifest(root);
    await fs.writeFile(file, 'changed prompt');
    assert.notEqual((await sourceManifest(root)).digest, before.digest);
    await fs.writeFile(file, 'baseline');
    await fs.writeFile(path.join(root, 'cap-runtime/agentic_cap/core.py'), 'changed runtime');
    assert.notEqual((await sourceManifest(root)).digest, before.digest);
  } finally { await fs.rm(root, { recursive: true, force: true }); }
});

test('Python provenance uses lexical filename order when one module extends another name', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'robot-source-order-'));
  try {
    for (const folder of ['runtime/robot', 'runtime/model-providers', 'cap-runtime/agentic_cap']) await fs.mkdir(path.join(root, folder), { recursive: true });
    for (const name of ['holding_watchdog.py', 'holding.py']) await fs.writeFile(path.join(root, 'cap-runtime/agentic_cap', name), name);
    const manifest = await sourceManifest(root);
    assert.deepEqual(manifest.files.map(f => f.path), ['cap-runtime/agentic_cap/holding.py', 'cap-runtime/agentic_cap/holding_watchdog.py']);
  } finally { await fs.rm(root, { recursive: true, force: true }); }
});
