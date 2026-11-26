"""Sequential development acceptance. Final and validation seeds are forbidden.

The real model receives canonical natural language and a scene, never a task
program. All episodes close before independent batch scoring begins.
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from agentic_cap.tabletop_campaign import TASKS,DEV_SEEDS
from agentic_cap.tabletop_evaluation import score_closed_batch


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--cases',default=','.join(t['id'] for t in TASKS))
    parser.add_argument('--seeds',default='201,202')
    parser.add_argument('--condition',choices=['direct_cap','system_no_memory'],default='system_no_memory')
    args=parser.parse_args();seeds=[int(s) for s in args.seeds.split(',')];names=args.cases.split(',')
    if not seeds or not set(seeds)<=set(DEV_SEEDS):parser.error('Development seeds only')
    cases=[next((t for t in TASKS if t['id']==name),None) for name in names]
    if any(t is None for t in cases):parser.error('Unknown case')
    project=Path(__file__).resolve().parents[2];output=args.output.resolve()
    if not output.is_relative_to(project):parser.error('Output must stay in the Thor project')
    output.mkdir(parents=True,exist_ok=False)
    node=Path.home()/'.local/share/agentic-robotics/tools/node/bin/node'
    execution=[];fixed=None
    for task in cases:
        for seed in seeds:
            folder=output/f'{task["id"]}-{seed}'
            row=dict(task=task['id'],seed=seed,condition=args.condition,folder=str(folder))
            with (output/'driver.log').open('a') as log:
                command=[str(node),str(project/'runtime/scripts/tabletop-model-trial.mjs'),'--output',str(folder),'--task',task['id'],'--seed',str(seed),'--condition',args.condition]
                process=subprocess.Popen(command,cwd=project,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                row['pid']=process.pid;(output/'active.json').write_text(json.dumps(row,indent=2))
                try:row['exit_code']=process.wait(timeout=660)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid,signal.SIGTERM)
                    try:process.wait(timeout=15)
                    except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL)
                    row.update(exit_code=-1,error='runner_wall_timeout; no automatic replay')
            protocol_file=folder/'protocol.json';result_file=folder/'result.json'
            if protocol_file.exists():
                protocol=json.loads(protocol_file.read_text())
                fingerprint=(protocol['configuration_fingerprint'],protocol['runtime_fingerprint'])
                if fixed is None:fixed=fingerprint
                elif fingerprint!=fixed:row['error']='source_or_model_changed_between_runs'
                row['protocol']=protocol
            if result_file.exists():
                result=json.loads(result_file.read_text());row.update(status=result['status'],task_wall_seconds=result.get('task_wall_seconds'),used=(result.get('episode') or {}).get('used'))
            execution.append(row);(output/'execution.json').write_text(json.dumps(execution,indent=2)+'\n')
            if row['exit_code'] or row.get('error'):raise RuntimeError('Development protocol failure; preserve records and diagnose before retrying')
    for row in execution:
        files=list((Path(row['folder'])/'physical').glob('*/episode.json'))
        if len(files)!=1 or any(json.loads(p.read_text())['status']!='closed' for p in files):raise ValueError('All tasks must be closed before scoring')
    scores=[]
    for row in execution:
        task=next(t for t in TASKS if t['id']==row['task'])
        scores.extend(dict(task=row['task'],condition=row['condition'],task_wall_seconds=row.get('task_wall_seconds'),**s)
            for s in score_closed_batch(Path(row['folder'])/'physical',task['goal']))
    (output/'independent-scores.json').write_text(json.dumps(scores,indent=2)+'\n')
    covered={s['task'] for s in scores if s.get('physical_verified') and s.get('public_verified')}
    false=sum(bool(s.get('false_completion')) for s in scores)
    summary=dict(stage='development',runs=len(scores),condition=args.condition,six_class_coverage=len(covered),
        physical_successes=sum(bool(s.get('physical_verified')) for s in scores),
        public_successes=sum(bool(s.get('public_verified')) for s in scores),false_completions=false,
        C_minimum_satisfied=covered=={t['id'] for t in TASKS} and not false,
        formal_test_seeds_used=False,statistics_claim='development diagnostics only, no statistical significance or frozen-test success rate')
    (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary))

if __name__=='__main__':main()
