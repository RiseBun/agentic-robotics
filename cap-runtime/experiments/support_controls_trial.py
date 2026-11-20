"""Physical inspection cancellation/goal-change diagnosis; no LLM or truth reads."""
import argparse
import copy
import json
import os
import time
from pathlib import Path

from agentic_cap.cap_backend import CaPBackend
from agentic_cap.core import Manager, StaleRevision
from experiments.active_view_trial import wait_for
from experiments.vertical_retreat_trial import RetreatTrial, source_hashes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--upstream', required=True)
    parser.add_argument('--records', type=Path, required=True)
    args = parser.parse_args()
    root = args.records.resolve()
    root.mkdir(parents=True, exist_ok=False)
    os.environ.update(CAP_HOLDING_WATCHDOG='1', CAP_ACTIVE_EVIDENCE='1', CAP_PLACEMENT_EVIDENCE='1',
                      CAP_SUPPORT_SECOND_VIEW='', CAP_SUPPORT_OCCLUSION='green_persistent')
    sources = source_hashes()
    (root/'protocol.json').write_text(json.dumps(dict(cases=['stop','goal_change'], task='cube_stack', seed=76,
        source_sha256=sources, controller='fixed public-sensor preparation; registered inspect_support',
        trigger='first view_attempt AND active physical motion', fault='persistent green RGB-D masking after verified red hold',
        scope='control protocol diagnosis; not model selection, task success rate, or natural occlusion'), indent=2))
    manager = Manager(root, lambda e: CaPBackend(e,args.upstream,evaluation=True))
    rows = []
    for mode in ['stop','goal_change']:
        state = manager.create('support-control-'+mode,'cube_stack',76,
                               dict(on=['red cube','green cube'],empty_gripper=True))
        e = manager.episodes[state['id']]
        row = dict(mode=mode,episode_id=e.id,passed=False,coverage_reached=False)
        try:
            wait_for(lambda:e.snapshot or e.status=='failed',150)
            if e.status != 'ready': raise RuntimeError('Initialization failed')
            trial = RetreatTrial(e,'support_active')
            trial.measure('initial')
            trial.pick('red cube')
            wait_for(lambda:e.observe().get('support_evidence',{}).get('green cube',{}).get('status')=='unknown',5)
            old_revision = e.revision
            e.decision('inspection',old_revision)
            sid = e.execute('inspection',old_revision,'inspect-support',
                            'inspect_support("green cube","red cube")\nopen_gripper()')
            wait_for(lambda:e.active==sid and e.motion and any(
                ev['kind']=='view_attempt' and ev.get('segment_id')==sid for ev in e.events),30)
            row['coverage_reached'] = True
            before_event = e.events[-1]['id']
            before = e.observe()
            if mode=='stop':
                row['control_result'] = e.cancel()
            else:
                e.update_goal(dict(holding='red cube'))
                row['control_result'] = dict(stopped=e.hold_ack >= e.hold_serial)
            wait_for(lambda:e.active is None,15)
            time.sleep(.5)  # Let a cancelled RPC worker attempt to finish.
            segment = copy.deepcopy(e.segments[sid])
            row['inspection_segment'] = segment
            row['late_old_api_starts'] = [ev['id'] for ev in e.events if ev['id']>before_event
                and ev['kind']=='api_started' and ev.get('segment_id')==sid]
            row['view_count'] = sum(ev['kind']=='view_attempt' and ev.get('segment_id')==sid for ev in e.events)
            row['release_after_cancel'] = any(ev['id']>before_event and ev['kind']=='api_result'
                and ev.get('name')=='open_gripper' for ev in e.events)
            if mode=='goal_change':
                try:
                    e.execute('stale-placement',old_revision,'old-plan','open_gripper()')
                except StaleRevision:
                    row['old_revision_rejected'] = True
                else:
                    raise RuntimeError('Old revision admitted')
            row['public_before_cancel'] = {k:before.get(k) for k in ['evidence_ref','tcp_xyz','tcp_wxyz']}
            row['post_cancel_probe'] = trial.stage('fresh-holding','get_execution_state()\ncheck_holding("red cube")')
            row['public_final'] = e.verify()
            row['goal'] = e.goal
            row['passed'] = bool(row['control_result']['stopped'] and segment['execution']=='cancelled'
                and row['view_count']==1 and not row['late_old_api_starts'] and not row['release_after_cancel']
                and e.gripper<.5)
            time.sleep(1.5)
        except Exception as exc:
            row['error'] = str(exc)
        finally:
            e.close()
            row.update(closed=e.state()['status'],used=e.state()['used'],source_unchanged=source_hashes()==sources)
            rows.append(row)
            (root/'results.json').write_text(json.dumps(rows,indent=2))
            print(json.dumps({k:row.get(k) for k in ['mode','episode_id','passed','coverage_reached','error','view_count']}),flush=True)
        if not row['source_unchanged']: raise RuntimeError('Frozen source changed')
    if not all(r['passed'] for r in rows): raise SystemExit(1)


if __name__=='__main__': main()
