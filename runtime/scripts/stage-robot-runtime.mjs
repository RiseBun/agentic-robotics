import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export async function stageRuntime(destination) {
  const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
  const seen = new Set();
  async function visit(relative) {
    if (seen.has(relative)) return;
    seen.add(relative);
    const source = await fs.readFile(path.join(root, relative), 'utf8');
    const imports = [...source.matchAll(/(?:\bfrom\s*|\bimport\s*)["']([^"']+)["']/g)].map(m => m[1]);
    for (const specifier of imports) {
      if (specifier.startsWith('node:')) continue;
      if (!specifier.startsWith('.')) throw new Error('Unexpected external dependency: ' + specifier);
      const target = path.normalize(path.join(path.dirname(relative), specifier));
      if (target.startsWith('..')) throw new Error('Runtime import leaves root');
      await visit(target);
    }
    const output = path.join(destination, relative);
    await fs.mkdir(path.dirname(output), { recursive: true });
    await fs.writeFile(output, source);
  }
  await visit('utility-entry.mjs');
  // The utility loader uses a dynamic import; admit only this specific app.
  await visit('application-service.mjs');
  const files = [...seen].map(p => p.replaceAll('\\', '/')).sort();
  const forbidden = /(?:ssh|hardware|browser|desktop-runtime|native-runtime|subagent|self-improve|resource-store|tool-registry|server\.mjs)/i;
  if (files.some(file => forbidden.test(file))) throw new Error('Removed feature entered the runtime bundle');
  await fs.writeFile(path.join(destination, 'package.json'), JSON.stringify({ name: 'embuddies-robot-runtime', version: '0.4.0', type: 'module', private: true }));
  await fs.writeFile(path.join(destination, 'robot-manifest.json'), JSON.stringify({ protocol: 1, files, decisions: ['observe', 'run_code', 'run_skill', 'ask_user', 'finish'] }, null, 2));
  return files;
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (!process.argv[2]) throw new Error('Provide an empty destination directory');
  console.log(JSON.stringify(await stageRuntime(path.resolve(process.argv[2]))));
}
