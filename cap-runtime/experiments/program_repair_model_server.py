"""Engineering-only public grasp and field-error preparation for model handoff."""
import argparse
import copy
import json
import os
from pathlib import Path

from agentic_cap.cap_backend import CaPBackend
from agentic_cap.core import Manager
from agentic_cap.server import runtime_provenance, serve
from experiments.active_view_trial import wait_for
from experiments.vertical_retreat_trial import RetreatTrial, source_hashes


GRASP = '''p, unused, extent = get_object_pose("green cube")
open_gripper()
goto_pose(p, [0,0,1,0], z_approach=0.10)
close_gripper()'''
INVALID = '''state = get_execution_state()
tcp_xyz = state[0]
tcp_wxyz = state[1]
lift_xyz = [tcp_xyz[0], tcp_xyz[1], tcp_xyz[2]+0.12]
goto_pose(lift_xyz, tcp_wxyz)
check_holding("green cube")'''
UNBOUND = '''g = get_object_pose("green cube")
p = g[0]
x = p[0]
y = p[1]
z = p[2]
q = [0,0,1,0]
open_gripper()
goto_pose([x,y,nz],q,z_approach=0.10)
close_gripper()'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--upstream', required=True)
    parser.add_argument('--records', type=Path, required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--seed', type=int, default=84)
    parser.add_argument('--mode', choices=['field_access','unbound_name'], default='field_access')
    args = parser.parse_args()
    root = args.records.resolve()
    if root.exists():
        raise RuntimeError('Use a new preparation directory; never replay')
    os.environ.update(CAP_ACTIVE_EVIDENCE='1', CAP_PLACEMENT_EVIDENCE='1', CAP_HOLDING_WATCHDOG='1',
                      CAP_SUPPORT_SECOND_VIEW='robot0_eye_in_hand', CAP_SUPPORT_OCCLUSION='')
    hashes = source_hashes()
    manager = Manager(root, lambda e: CaPBackend(e,args.upstream,evaluation=True))
    state = manager.create('program-repair-'+args.mode+'-'+str(args.seed), 'cube_restack', args.seed,
                           dict(on=['red cube','green cube'],empty_gripper=True))
    e = manager.episodes[state['id']]
    trial = RetreatTrial(e,'support_active')
    diagnostic = dict(mode=args.mode, prepared=False, episode_id=e.id, seed=args.seed,
        source_sha256=hashes, attempts=[],
        scope='Fixed invalid program preparation; model alone repairs and continues the original restack goal')
    try:
        wait_for(lambda:e.snapshot or e.status=='failed',150)
        if e.status!='ready':
            raise RuntimeError('Initialization failed')
        e.decision('initial-observation',e.revision)
        trial.measure('initial')
        if args.mode=='field_access':
            before = e.observe()
            good = trial.stage('grasp-green',GRASP)
            diagnostic['attempts'].append(dict(observation=before,segment_id=good['id']))
        before = e.observe()
        bad = trial.stage('invalid-'+args.mode,INVALID if args.mode=='field_access' else UNBOUND,expected='failed')
        diagnostic['attempts'].append(dict(observation=before,segment_id=bad['id']))
        feedback = bad.get('feedback',{})
        expected_kind='invalid_subscript' if args.mode=='field_access' else 'unbound_name'
        attempts_ok=(len(bad['api_attempts'])==1 and bad['api_attempts'][0]['name']=='get_execution_state'
                     if args.mode=='field_access' else not bad['api_attempts'])
        if feedback.get('category')!='invalid_program' or feedback.get('evidence',{}).get('kind')!=expected_kind or not attempts_ok:
            raise RuntimeError('Expected bounded program-error preparation without later actions')
        if source_hashes()!=hashes:
            raise RuntimeError('Frozen source changed')
        event = e.event('diagnostic_model_handoff_ready',mode=args.mode,
                        fixed_segment_ids=[a['segment_id'] for a in diagnostic['attempts']],used=e.state()['used'])
        diagnostic.update(prepared=True,handoff_event_id=event['id'],preparation_used=e.state()['used'])
    except Exception as exc:
        diagnostic['error'] = str(exc)
        e.close()
    (root/'preparation.json').write_text(json.dumps(diagnostic,indent=2))
    provenance = runtime_provenance('cap',args.upstream)
    provenance['diagnostic'] = copy.deepcopy(diagnostic)
    server = serve(manager,'127.0.0.1',args.port,os.environ['CAP_RUNTIME_TOKEN'],provenance)
    print(json.dumps({k:diagnostic.get(k) for k in ['mode','prepared','episode_id','error']}),flush=True)
    try:
        server.serve_forever()
    finally:
        e.close()
        server.server_close()


if __name__=='__main__':
    main()
