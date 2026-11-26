// Real model, synthetic Runtime cuts, no robot execution. Not a policy benchmark.
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { sourceManifest } from './robot-campaign.mjs';

export async function runUnifiedProbe(options, configuration) {
  const root = path.resolve(options.sourceRoot), output = path.resolve(options.output);
  await fs.mkdir(output, { recursive: true });
  const sources = await sourceManifest(root);
  const channel = (await configuration()).active;
  const model = { model: channel.model, api: channel.api, baseURL: channel.baseURL, reasoningEffort: channel.reasoningEffort };
  const cases = ['stack_start', 'restack_start', 'clear_held', 'support_unknown'];
  const manifest = { scope: 'Real-model decision probe on synthetic Runtime facts; no images or actions; not physical/LLM task success',
    cases, attempts_per_case: 1, sources, model };
  await fs.writeFile(path.join(output, 'manifest.json'), JSON.stringify(manifest, null, 2));
  const { requestDecision } = await import(pathToFileURL(path.join(root, 'runtime/robot/model.mjs')).href);
  const rows = [];
  for (const name of cases) {
    const target = path.join(output, name);
    await fs.mkdir(target, { recursive: true });
    if ((await sourceManifest(root)).digest !== sources.digest) throw new Error('Probe source changed');
    const { stdout } = await promisify(execFile)(options.python, [path.join(root, 'cap-runtime/experiments/unified_decision_cases.py'), name],
      { cwd: path.join(root, 'cap-runtime'), windowsHide: true, maxBuffer: 2_000_000 });
    const specimen = JSON.parse(stdout);
    await fs.writeFile(path.join(target, 'input.json'), JSON.stringify(specimen.context, null, 2));
    const started = Date.now();
    let result;
    try {
      const decision = await requestDecision(channel, specimen.context, [], AbortSignal.timeout(90000),
        async (kind, value) => fs.appendFile(path.join(target, 'model.jsonl'), JSON.stringify({ kind, ...value })+'\n'));
      const ref = decision.decision === 'run_skill'
        ? { state_id: decision.state_id, task_node: decision.task_node } : decision.task_ref;
      const expected = specimen.expected;
      result = { case: name, decision, expected,
        correct_node: ref?.task_node === expected.node_id && ref?.state_id === specimen.context.robot_system.state_id,
        correct_route: decision.decision === expected.decision,
        correct_arguments: !expected.skill || (decision.skill?.name === expected.skill
          && decision.skill?.arguments?.object === expected.object),
        actions_executed: 0, wall_milliseconds: Date.now()-started };
    } catch (error) {
      result = { case: name, error: error.message, actions_executed: 0, wall_milliseconds: Date.now()-started };
    }
    rows.push(result);
    await fs.writeFile(path.join(output, 'results.json'), JSON.stringify(rows, null, 2));
  }
  return { scope: manifest.scope, model, cases: rows };
}
