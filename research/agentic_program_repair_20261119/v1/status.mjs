import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root=path.dirname(fileURLToPath(import.meta.url));
const diagnostic=path.join(root,'repair-campaign/field_access/result.json');
if (fs.existsSync(diagnostic)) {
  const result=JSON.parse(fs.readFileSync(diagnostic));
  console.log(JSON.stringify({variant:'repair',outcome:result.outcome,episode_id:result.episode_id,close_error:result.close_error}));
}
const campaign=path.join(root,'normal-campaign');
if (!fs.existsSync(path.join(campaign,'campaign.json'))) {
  console.log(JSON.stringify({variant:'normal',status:'not_admitted'}));
} else for (const trial of JSON.parse(fs.readFileSync(path.join(campaign,'campaign.json'))).plan) {
  const folder=path.join(campaign,trial.trial_id),result=path.join(folder,'result.json');
  if (fs.existsSync(result)) {
    const row=JSON.parse(fs.readFileSync(result));
    console.log(JSON.stringify({variant:'normal',task:trial.task,outcome:row.outcome,public:row.public_at_decision?.status,episode_id:row.episode_id,seconds:row.policy_seconds}));
    continue;
  }
  const records=path.join(folder,'records');
  const directory=fs.existsSync(records)?fs.readdirSync(records,{withFileTypes:true}).find(e=>e.isDirectory())?.name:null;
  const file=directory&&path.join(records,directory,'workbench.jsonl');
  if (!file || !fs.existsSync(file)) { console.log(JSON.stringify({variant:'normal',task:trial.task,status:'not_admitted'})); continue; }
  const rows=fs.readFileSync(file,'utf8').trim().split('\n').flatMap(s=>{try{return [JSON.parse(s)];}catch{return [];}});
  console.log(JSON.stringify({variant:'normal',task:trial.task,status:'running',latest:rows.filter(r=>['decision','segment_feedback','assistance_requested','agent_error'].includes(r.kind)).slice(-3).map(r=>({kind:r.kind,decision:r.decision?.decision,subgoal:r.decision?.subgoal,feedback:r.segment?.feedback?.category,error:r.error}))}));
}
