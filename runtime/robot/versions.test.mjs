import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import {sourceDigest,deploymentStatus} from './versions.mjs';
test('missing runtime provenance remains unknown; changed running code requires restart',()=>{
 assert.equal(deploymentStatus({installedRuntime:'new'}).runtime,'unknown');
 assert.equal(deploymentStatus({runningRuntime:'old',installedRuntime:'new'}).runtime,'restart_required');
 assert.equal(deploymentStatus({runningRuntime:'same',installedRuntime:'same'}).runtime,'current');
 assert.equal(deploymentStatus({loadedWorkbench:'old',installedWorkbench:'new'}).workbench,'restart_required');
});
test('runtime disk digest matches Python provenance framing and detects untracked source',async()=>{
 const folder=await fs.mkdtemp(path.join(os.tmpdir(),'source-version-'));
 try {
  const h=s=>crypto.createHash('sha256').update(s).digest('hex');
  await fs.writeFile(path.join(folder,'a.py'),'source');
  assert.equal(await sourceDigest(folder,'.py','agentic_cap/'),h('agentic_cap/a.py\0'+h('source')+'\n'));
  const before=await sourceDigest(folder,'.py','agentic_cap/');
  await fs.writeFile(path.join(folder,'new.py'),'changed');
  assert.notEqual(await sourceDigest(folder,'.py','agentic_cap/'),before);
 } finally {await fs.rm(folder,{recursive:true,force:true});}
});
