import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root=path.dirname(fileURLToPath(import.meta.url));
for(const variant of ['normal']){
  const folder=path.join(root,variant+'-campaign');
  const manifest=path.join(folder,'campaign.json');
  if(!fs.existsSync(manifest)){console.log(JSON.stringify({variant,status:'not_admitted'}));continue;}
  const plan=JSON.parse(fs.readFileSync(manifest)).plan;
  for(const trial of plan){
    const trialRoot=path.join(folder,trial.trial_id),result=path.join(trialRoot,'result.json');
    if(fs.existsSync(result)){
      const r=JSON.parse(fs.readFileSync(result));console.log(JSON.stringify({variant,task:trial.task,outcome:r.outcome,public:r.public_at_decision?.status,episode_id:r.episode_id,seconds:r.policy_seconds}));continue;
    }
    const records=path.join(trialRoot,'records');
    const directory=fs.existsSync(records)?fs.readdirSync(records,{withFileTypes:true}).find(e=>e.isDirectory())?.name:null;
    const file=directory&&path.join(records,directory,'workbench.jsonl');
    if(!file||!fs.existsSync(file)){console.log(JSON.stringify({variant,task:trial.task,status:'not_admitted'}));continue;}
    const rows=fs.readFileSync(file,'utf8').trim().split('\n').flatMap(line=>{try{return[JSON.parse(line)];}catch{return[];}});
    const selected=rows.filter(r=>['decision','segment_feedback','assistance_requested','agent_error'].includes(r.kind)||r.kind==='runtime_event'&&['public_holding_contradicted','public_holding_hold_confirmed'].includes(r.event?.kind));
    console.log(JSON.stringify({variant,task:trial.task,status:'running',latest:selected.slice(-3).map(r=>({kind:r.kind,decision:r.decision?.decision,subgoal:r.decision?.subgoal,feedback:r.segment?.feedback?.category,event:r.event?.kind,origin:r.origin,error:r.error}))}));
  }
}
