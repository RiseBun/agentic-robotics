import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root=path.dirname(fileURLToPath(import.meta.url)),folder=path.join(root,'repair-campaign/unbound_name');
if (fs.existsSync(path.join(folder,'result.json'))) {
  const r=JSON.parse(fs.readFileSync(path.join(folder,'result.json')));
  console.log(JSON.stringify({outcome:r.outcome,public:r.public_at_decision?.status,episode_id:r.episode_id,close_error:r.close_error}));
} else {
  const records=path.join(folder,'records');
  const directory=fs.existsSync(records)?fs.readdirSync(records,{withFileTypes:true}).find(e=>e.isDirectory())?.name:null;
  const file=directory&&path.join(records,directory,'workbench.jsonl');
  const rows=file&&fs.existsSync(file)?fs.readFileSync(file,'utf8').trim().split('\n').flatMap(s=>{try{return [JSON.parse(s)];}catch{return [];}}):[];
  console.log(JSON.stringify({status:rows.length?'running':'not_admitted',latest:rows.filter(r=>['decision','segment_feedback','assistance_requested','agent_error'].includes(r.kind)).slice(-3).map(r=>({kind:r.kind,decision:r.decision?.decision,subgoal:r.decision?.subgoal,feedback:r.segment?.feedback?.category,error:r.error}))}));
}
