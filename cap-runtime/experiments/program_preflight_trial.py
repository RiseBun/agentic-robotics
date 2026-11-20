"""Late typo must not release a physically held object before rejection."""
import argparse
import copy
import json
import os
import time
from pathlib import Path

from agentic_cap.cap_backend import CaPBackend
from agentic_cap.core import Manager
from experiments.active_view_trial import wait_for
from experiments.vertical_retreat_trial import RetreatTrial, source_hashes


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--upstream', required=True)
    p.add_argument('--records', type=Path, required=True)
    args = p.parse_args()
    root = args.records.resolve()
    root.mkdir(parents=True, exist_ok=False)
    os.environ.update(CAP_HOLDING_WATCHDOG='1', CAP_ACTIVE_EVIDENCE='1', CAP_PLACEMENT_EVIDENCE='1',
                      CAP_SUPPORT_SECOND_VIEW='', CAP_SUPPORT_OCCLUSION='')
    hashes = source_hashes()
    (root/'protocol.json').write_text(json.dumps(dict(task='cube_lifting', seed=71, source_sha256=hashes,
        code='open_gripper()\ngoto_pose([0,0,nz],[0,0,1,0])',
        purpose='late undefined variable must reject entire segment without releasing held object',
        scope='one fixed-controller physical regression; no model or success-rate comparison'), indent=2))
    manager = Manager(root, lambda e: CaPBackend(e, args.upstream, evaluation=True))
    state = manager.create('preflight-held-typo', 'cube_lifting', 71, dict(holding='red cube'))
    e = manager.episodes[state['id']]
    row = dict(episode_id=e.id, passed=False)
    try:
        wait_for(lambda:e.snapshot or e.status=='failed',150)
        if e.status!='ready': raise RuntimeError('Initialization failed')
        trial = RetreatTrial(e,'support_active')
        trial.measure('initial')
        trial.pick('red cube')
        before_calls = e.used['calls']
        before_events = len(e.events)
        bad = trial.stage('late-unbound-name', 'open_gripper()\ngoto_pose([0,0,nz],[0,0,1,0])', expected='failed')
        row['bad_segment'] = bad
        row['bad_api_delta'] = e.used['calls']-before_calls
        row['bad_api_started'] = [x for x in e.events[before_events:] if x['kind']=='api_started']
        if row['bad_api_delta'] or row['bad_api_started'] or e.gripper >= .5:
            raise RuntimeError('Invalid program caused an API or gripper change')
        if bad['feedback']['category']!='invalid_program' or "'nz'" not in bad['error']:
            raise RuntimeError('Wrong preflight rejection evidence')
        good = trial.stage('corrected-holding-check','s=get_execution_state()\ncheck_holding("red cube")')
        row['corrected_segment'] = copy.deepcopy(good)
        row['public_final'] = e.finish(e.revision)
        row['passed'] = row['public_final']['status']=='verified'
        # Preserve the accepted-completion window for the independent scorer.
        # No private samples are read by this controller.
        if row['passed']:
            before_step = e.snapshot['control_step']
            time.sleep(1.5)
            row['settle_control_steps'] = e.snapshot['control_step']-before_step
    except Exception as exc: row['error']=str(exc)
    finally:
        e.close()
        row.update(closed=e.state()['status'],used=e.state()['used'],source_unchanged=source_hashes()==hashes)
        (root/'result.json').write_text(json.dumps(row,indent=2))
        print(json.dumps({k:row.get(k) for k in ['episode_id','passed','error','bad_api_delta','public_final']}),flush=True)
    if not row['passed'] or not row['source_unchanged']: raise SystemExit(1)


if __name__=='__main__':main()
