// Real-model historical public-state wording probe. No Runtime client or actions.
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {localCrypto} from '../../runtime/robot/local-crypto.mjs';
import {createConfigStore} from '../../runtime/config-store.mjs';
import {requestDecision} from '../../runtime/robot/model.mjs';
import {sourceManifest} from '../../runtime/scripts/robot-campaign.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const target=path.join(root,'research/stage_resources_20261122/assistance-probe');
await fs.mkdir(target,{recursive:false});
const physical=path.join(root,'.tmp/stage-resources-20261122/cube_restack-s22/campaign/cube_restack-s22-r0');
const state=JSON.parse(await fs.readFile(path.join(physical,'state-at-terminal.json'),'utf8'));
const context={mode:'execution',scenario:'cube_restack',
  user_goal:'把绿色方块从红色方块上移开，再将红色方块放在绿色方块上，释放后空夹爪。',
  goal:state.episode.goal,revision:state.episode.revision,latest_observation:state.observation,
  robot_system:state.observation.system,used_budget:state.episode.used,limits:state.episode.limits,
  current_subgoal:state.subgoal,remaining_steps:state.remaining_steps,
  recent_attempts:state.history.slice(-6).map(({events,...segment})=>segment),
  uncertainty:'Historical public cut re-evaluated offline; no actions or new facts. Assess resources and assistance wording only.'};
const data=path.join(process.env.HOME,'.local/share/agentic-robotics/data');
globalThis.embuddiesCrypto=await localCrypto(data);
const config=createConfigStore(path.join(root,'runtime'),data);
const channel=(await config.read()).active;
const sources=await sourceManifest(root);
await fs.writeFile(path.join(target,'input.json'),JSON.stringify(context,null,2));
const decision=await requestDecision(channel,context,[],AbortSignal.timeout(90000),
  (kind,value)=>fs.appendFile(path.join(target,'model.jsonl'),JSON.stringify({kind,...value})+'\n'));
const result={scope:'Real model historical public-state wording check; no physical trial',
  actions_executed:0,decision,sources,source_unchanged:(await sourceManifest(root)).digest===sources.digest};
await fs.writeFile(path.join(target,'result.json'),JSON.stringify(result,null,2));
console.log(JSON.stringify({scope:result.scope,actions_executed:0,decision,source_unchanged:result.source_unchanged}));
