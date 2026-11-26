import fs from 'node:fs/promises';
import path from 'node:path';
import crypto from 'node:crypto';
const digest=value=>crypto.createHash('sha256').update(value).digest('hex');
export async function sourceDigest(directory,extension,prefix='') {
  const names=(await fs.readdir(directory)).filter(n=>n.endsWith(extension)&&!n.endsWith('.test.mjs')).sort();
  const rows=await Promise.all(names.map(async n=>prefix+n+'\0'+digest(await fs.readFile(path.join(directory,n)))+'\n'));
  return digest(rows.join(''));
}
export function deploymentStatus({runningRuntime,installedRuntime,loadedWorkbench,installedWorkbench}) {
  const runtime=runningRuntime&&installedRuntime?(runningRuntime===installedRuntime?'current':'restart_required'):'unknown';
  const workbench=loadedWorkbench&&installedWorkbench?(loadedWorkbench===installedWorkbench?'current':'restart_required'):'unknown';
  return {runtime,workbench,runningRuntime,installedRuntime,loadedWorkbench,installedWorkbench};
}
