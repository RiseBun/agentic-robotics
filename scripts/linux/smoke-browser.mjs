// Native browser acceptance. --run-task submits one real lifting goal to the
// existing host/Runtime/model, and records its outcome without asserting success.
import fs from 'node:fs/promises';
import path from 'node:path';
import { chromium } from '../../runtime/node_modules/playwright/index.mjs';

const output = path.resolve(process.env.ROBOT_SMOKE_OUTPUT || '.tmp/linux-browser-smoke');
await fs.mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: 'chromium', headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const errors = [];
page.on('pageerror', error => errors.push(error.message));
const state = () => page.evaluate(async () => (await fetch('/api/robot/state')).json());
async function downloadRecords() {
  const [download] = await Promise.all([page.waitForEvent('download'),
    page.getByRole('button', { name: '导出任务记录', exact: true }).click()]);
  const file = path.join(output, 'task-export.json');
  await download.saveAs(file);
  const value = JSON.parse(await fs.readFile(file, 'utf8'));
  if (!value.task_id || !value.runtime_zip_base64) throw new Error('Incomplete downloaded task package');
  return { task_id: value.task_id, bytes: (await fs.stat(file)).size };
}
try {
  await page.goto('http://127.0.0.1:8787');
  await page.getByRole('heading', { name: '告诉机器人要做什么' }).waitFor();
  await page.getByRole('button', { name: '设置', exact: true }).click();
  await page.getByRole('dialog').waitFor();
  const settings = await page.evaluate(async () => (await fetch('/api/robot/settings')).json());
  const model = settings.channels.find(c => c.id === settings.activeChannel);
  if (!model?.model || !settings.hasRuntimeToken) throw new Error('Missing migrated model/Runtime settings');
  await page.getByRole('button', { name: '关闭设置', exact: true }).click();
  const result = { browser_ui: true, model: model.model, runtime_url: settings.runtimeURL,
    scope: 'Migration acceptance, one development task; not a success-rate experiment' };
  if (process.argv.includes('--run-task')) {
    const before = await state();
    if (before.episode && !['closed','interrupted'].includes(before.episode.status)) {
      throw new Error('An existing scene is active; refusing to interrupt it');
    }
    await page.getByLabel('实验场景').selectOption('cube_lifting');
    await page.getByLabel('任务目标或新指令').fill('抓起红色方块并保持抬起，不要释放。');
    await page.getByRole('button', { name: '开始任务', exact: true }).click();
    const deadline = Date.now()+340_000;
    let current;
    while (Date.now() < deadline) {
      current = await state();
      if (current.task_id && ['completed','needs_attention','awaiting_user','stopped','failed','interrupted','closed'].includes(current.status)) break;
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
    if (!current?.task_id) throw new Error('No task was created');
    const image = page.getByAltText('Runtime 最新场景观测');
    result.observation_image = await image.count() > 0 && await image.evaluate(el => el.complete && el.naturalWidth > 0);
    Object.assign(result, { status: current.status, task_id: current.task_id, episode_id: current.episode?.id,
      goal_verification: current.verification, budget: current.episode?.used,
      executed_segments: Object.values(current.episode?.segments || {}).map(s => ({
        id:s.id, execution:s.execution, skill:s.skill?.name, verification:s.skill_verification?.status, error:s.error })) });
    await page.screenshot({ path:path.join(output,'task.png'),fullPage:true });
    await fs.writeFile(path.join(output,'state.json'),JSON.stringify(current,null,2));
    result.download = await downloadRecords();
    await page.evaluate(async () => fetch('/api/robot/close',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}));
  } else {
    if (process.argv.includes('--export-existing')) result.download = await downloadRecords();
    await page.screenshot({ path:path.join(output,'workbench.png'),fullPage:true });
  }
  result.page_errors = errors;
  await fs.writeFile(path.join(output,'result.json'),JSON.stringify(result,null,2));
  console.log(JSON.stringify(result));
  if (errors.length) throw new Error('Browser JavaScript errors');
} finally { await browser.close(); }
