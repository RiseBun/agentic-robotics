"""Fixed-controller same-scene diagnostic: primary, dual, all-views-masked.

The secondary sensor is enabled at the identical prepared release phase. No
model calls, reset, threshold changes or private feedback during execution.
"""
import argparse
import copy
import json
import os
import time
from pathlib import Path
from agentic_cap.core import Manager
from agentic_cap.cap_backend import CaPBackend
from agentic_cap.active_evidence import support_evidence
from experiments.active_view_trial import wait_for
from experiments.vertical_retreat_trial import RetreatTrial, source_hashes


def prepare(e):
    import numpy as np
    trial=RetreatTrial(e,'support_active')
    trial.measure('initial')
    measurement=trial.pick('green cube')
    trial.place(measurement,'green cube','red cube',aside=True)
    measurement=trial.pick('red cube')
    measurement=trial.prepare_placement(measurement,'red cube','green cube')
    obj,_=trial.center(measurement,'red cube'); support,_=trial.center(measurement,'green cube')
    prior=measurement['objects']['green cube']['size_reference']
    size=prior['side_m'] if prior else max(measurement['objects']['green cube']['visible']['surface_span_xyz'][:2])
    support[2]+=size
    low=(support-(obj-np.asarray(measurement['tcp_xyz']))).tolist()
    q=measurement['tcp_wxyz']; high=[low[0],low[1],low[2]+.12]
    trial.stage('release-origin',f'goto_pose({high!r}, {q!r})')
    state=e.observe(); target=list(state['tcp_xyz']); target[2]=low[2]
    trial.stage('release-height',f'goto_pose({target!r}, {state["tcp_wxyz"]!r}, z_approach=0.0)')
    return trial


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--upstream',required=True); p.add_argument('--records',type=Path,required=True)
    args=p.parse_args(); root=args.records.resolve(); root.mkdir(parents=True,exist_ok=False)
    os.environ.update(CAP_ACTIVE_EVIDENCE='1',CAP_PLACEMENT_EVIDENCE='1',CAP_SUPPORT_SECOND_VIEW='',CAP_SUPPORT_OCCLUSION='')
    hashes=source_hashes(); cases=['single','dual','dual_masked']
    (root/'protocol.json').write_text(json.dumps(dict(cases=cases,task='cube_restack',seed=66,source_sha256=hashes,
        controller='same public geometry release phase, no LLM',
        secondary_activation='diagnostic-only after public preparation; no movement or reset',
        scope='seen-failure sensor diagnosis, not natural-task success rate'),indent=2))
    results=[]
    for mode in cases:
        manager=Manager(root/mode,lambda e:CaPBackend(e,args.upstream,evaluation=True))
        state=manager.create('second-view-'+mode,'cube_restack',66,dict(on=['red cube','green cube'],empty_gripper=True))
        e=manager.episodes[state['id']]; row=dict(mode=mode,episode_id=e.id,status='failed')
        try:
            wait_for(lambda:e.snapshot or e.status=='failed',150)
            if e.status!='ready': raise RuntimeError('Backend initialization failed')
            trial=prepare(e)
            initial=e.observe(); before=support_evidence(initial,'green cube',e.backend.reference)
            row['before']=before
            if before['status']!='unknown': raise RuntimeError('Seen release-position visibility failure not reproduced')
            with e.lock:
                step=e.backend.tick
                if mode!='single': e.backend.secondary_camera='robot0_eye_in_hand'
                if mode=='dual_masked': e.backend.support_occlusion_mode='green_persistent'
                e.event('diagnostic_secondary_sensor_activated',mode=mode,control_step=step,camera_name=e.backend.secondary_camera)
            def published():
                s=e.observe()
                return s if s.get('control_step',-1)>step+5 and (mode=='single' or 'camera_views' in s) else None
            observed=wait_for(published,10)
            if mode=='dual':
                def available():
                    s=e.observe()
                    return s if support_evidence(s,'green cube',e.backend.reference)['status']=='available' else None
                observed=wait_for(available,10)
            row['after']=support_evidence(observed,'green cube',e.backend.reference)
            row['observation']=copy.deepcopy(observed)
            if mode=='dual':
                if row['after'].get('camera_name')!='public_rgbd_union': raise RuntimeError('Expected actual complementary-point evidence')
                trial.stage('release-and-clear','open_gripper()\ns=get_execution_state()\np=s["tcp_xyz"]\n'
                            'q=s["tcp_wxyz"]\np[2]=p[2]+0.13\ngoto_pose(p,q,z_approach=0.0)')
                # Registered post-action verification, with the original window/thresholds.
                row['verification']=e.finish(e.revision)
            else:
                trial.stage('unknown-must-block-release','open_gripper()',expected='failed')
                row['verification']=e.finish(e.revision)
            before_sim=e.observe()['simulation_time']; started=time.monotonic(); time.sleep(5)
            elapsed=time.monotonic()-started; advanced=e.observe()['simulation_time']-before_sim
            row['continuous_simulation']=dict(wall_seconds=elapsed,sim_seconds=advanced,ratio=advanced/elapsed)
            expected='verified' if mode=='dual' else 'unknown'
            row['passed']=(row['verification']['status']==expected and advanced>=4.5
                           and (mode=='dual' or e.gripper<.5))
            if mode!='single' and mode!='dual' and row['after']['status']!='unknown':row['passed']=False
            row['status']='completed' if row['passed'] else 'failed'
        except Exception as exc:row['error']=str(exc)
        finally:
            e.close();row.update(closed=e.state()['status'],used=e.state()['used'],source_unchanged=source_hashes()==hashes)
            results.append(row);(root/'results.json').write_text(json.dumps(results,indent=2))
            print(json.dumps({k:row.get(k) for k in ['mode','status','error','verification','continuous_simulation']}),flush=True)
    if not all(r.get('passed') and r['source_unchanged'] for r in results):raise SystemExit(1)


if __name__=='__main__':main()
