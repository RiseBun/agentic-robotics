"""Freeze and run admitted 54-condition campaigns. Does not promote skills."""
import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path
from agentic_cap.tabletop_campaign import campaign_manifest,admit_campaign,TASKS
from agentic_cap.tabletop_memory import SkillLibrary
from agentic_cap.tabletop_evaluation import score_closed_batch


def closed_run_result(output,exit_code):
    """Task failure is data, not permission to drop a frozen condition."""
    output=Path(output);result_file=output/'result.json'
    if exit_code or not result_file.exists():raise RuntimeError('Runner protocol failure; no automatic replay')
    files=list((output/'physical').glob('*/episode.json'))
    if len(files)!=1:raise RuntimeError('Expected one physical episode per frozen condition')
    state=json.loads(files[0].read_text())
    if state['status']!='closed' or state.get('active_segment') is not None:raise RuntimeError('Physical close unconfirmed; stop campaign')
    return json.loads(result_file.read_text())


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--protocol',required=True,type=Path)
    parser.add_argument('--gates',required=True,type=Path);parser.add_argument('--library',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path);parser.add_argument('--run',action='store_true')
    args=parser.parse_args();protocol=json.loads(args.protocol.read_text());gates=json.loads(args.gates.read_text())
    if not args.library.is_dir():parser.error('Validated library directory does not exist')
    library=SkillLibrary(args.library)
    entries=[json.loads(f.read_text()) for f in args.library.glob('*.json')]
    if not entries or any(c.get('state')!='library' for c in entries):parser.error('Use a separate library containing published, independently validated candidates only')
    manifest=campaign_manifest(protocol['configuration_fingerprint'],protocol['runtime_fingerprint'],library.fingerprint())
    admit_campaign(gates,manifest,library.fingerprint())
    # Source gates must identify their evidence. A bare passed label is not a report.
    for name in ['A','B','C','D_validation']:
        if not gates[name].get('evidence'):parser.error('Gate '+name+' is missing evidence')
    args.output.mkdir(parents=True,exist_ok=False);manifest_file=args.output/'campaign.json'
    manifest_file.write_text(json.dumps(manifest,indent=2)+'\n');(args.output/'gates.json').write_text(json.dumps(gates,indent=2)+'\n')
    if not args.run:print(json.dumps(dict(status='frozen_not_started',manifest=str(manifest_file))));return
    project=Path(__file__).resolve().parents[2]
    node=Path.home()/'.local/share/agentic-robotics/tools/node/bin/node'
    results=[]
    for index,run in enumerate(manifest['runs']):
        admit_campaign(gates,manifest,library.fingerprint())
        output=args.output/f'{index:02d}-{run["task"]}-{run["seed"]}-{run["condition"]}'
        command=[str(node),str(project/'runtime/scripts/tabletop-model-trial.mjs'),'--output',str(output),
            '--task',run['task'],'--seed',str(run['seed']),'--condition',run['condition'],
            '--library',str(args.library),'--campaign',str(manifest_file)]
        with (args.output/'campaign.log').open('a') as log:
            process=subprocess.run(command,cwd=project,stdout=log,stderr=subprocess.STDOUT,timeout=660)
        row=dict(run,index=index,exit_code=process.returncode,result=closed_run_result(output,process.returncode))
        results.append(row);(args.output/'execution.json').write_text(json.dumps(results,indent=2)+'\n')
        # Failed/unknown/budget-exhausted tasks stay in the frozen denominator.
    # Every isolated runner must close before the first private score is read.
    for row in results:
        directory=args.output/f'{row["index"]:02d}-{row["task"]}-{row["seed"]}-{row["condition"]}'/'physical'
        for f in directory.glob('*/episode.json'):
            if json.loads(f.read_text())['status']!='closed':raise ValueError('An episode is still open; scoring refused')
    scores=[]
    for row in results:
        directory=args.output/f'{row["index"]:02d}-{row["task"]}-{row["seed"]}-{row["condition"]}'/'physical'
        scores.extend(dict(task=row['task'],condition=row['condition'],**score) for score in score_closed_batch(directory,next(t['goal'] for t in TASKS if t['id']==row['task'])))
    if library.fingerprint()!=manifest['library_fingerprint']:raise ValueError('Skill library changed during evaluation')
    (args.output/'independent-scores.json').write_text(json.dumps(scores,indent=2)+'\n')
    false_completions=sum(bool(s.get('false_completion')) for s in scores)
    unscored=sum(s.get('status')!='scored' for s in scores)
    summary=dict(status='release_blocked' if false_completions or unscored else 'evaluated_not_automatically_promoted',
        runs=len(results),false_completions=false_completions,unscored=unscored,statistics_claim='descriptive small-sample results only')
    (args.output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n');print(json.dumps(summary))

if __name__=='__main__':main()
