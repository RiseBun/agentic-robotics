"""Closed-record read-only preview validation. Never reads private evaluator files."""
import argparse,ast,copy,hashlib,json,os,sys
from pathlib import Path
import numpy as np
from agentic_cap.cartesian_motion import solve_pose_candidate
from agentic_cap.public_sensors import PublicExecutionMonitor
from experiments.placement_preview import predict_obstruction


def packet(path):
    with np.load(path,allow_pickle=False) as data:
        return {k:data[k].item() if data[k].ndim==0 else data[k] for k in data.files}


def candidate(program):
    tree=ast.parse(program)
    first=next(n for n in tree.body if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and getattr(n.value.func,'id',None)=='goto_pose')
    xyz=ast.literal_eval(first.value.args[0]);quat=ast.literal_eval(first.value.args[1])
    z=next(n.value for n in tree.body if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Subscript)
        and getattr(n.targets[0].value,'id',None)=='p' and isinstance(n.targets[0].slice,ast.Constant) and n.targets[0].slice.value==2)
    xyz[2]=ast.literal_eval(z)
    return dict(xyz=xyz,wxyz=quat)


def main():
    p=argparse.ArgumentParser();p.add_argument('--project',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True);a=p.parse_args();root=a.project.resolve();a.output.mkdir(parents=True,exist_ok=False)
    records=root/'.tmp/release-view-orientation-20261123';rows=json.loads((records/'results.json').read_text())
    # Validate the complete four-condition set before any analysis.
    for row in rows:
        folder=records/f'seed{row["seed"]}-yaw{row["support_yaw_degrees"]}'/row['episode_id']
        episode=json.loads((folder/'episode.json').read_text())
        assert episode['status']=='closed' and row['source_unchanged']
    sys.path.insert(0,str(root/'third_party/cap-x'))
    from capx.integrations.motion.pyroki import init_pyroki
    import pyroki
    from robot_descriptions.loaders.yourdfpy import load_robot_description
    fk=PublicExecutionMonitor(pyroki.Robot.from_urdf(load_robot_description('panda_description')),None).fk
    solver=init_pyroki(server_url='http://127.0.0.1:8116')
    results=[]
    for row in rows:
        folder=records/f'seed{row["seed"]}-yaw{row["support_yaw_degrees"]}'/row['episode_id']
        episode=json.loads((folder/'episode.json').read_text());entry=dict(seed=row['seed'],support_yaw_degrees=row['support_yaw_degrees'],source_status=row['status'],actions_executed=0,views=[])
        if row['status']=='captured':
            primary=packet(folder/'view-high-robot0_robotview.npz');xyz,quat=fk(primary['joint_positions'])
            current=dict(xyz=xyz.tolist(),wxyz=quat.tolist());target=candidate(row['pre_release_program'])
            entry['candidate_source']='Registered program compiled before the recorded descent; no low-frame object geometry used.'
            entry['candidate']=target;entry['source_tcp']=current
            # Preview the same high approach and low endpoint without stepping an environment.
            tree=ast.parse(row['pre_release_program']);first=next(n.value for n in tree.body if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and getattr(n.value.func,'id',None)=='goto_pose')
            above=dict(xyz=ast.literal_eval(first.args[0]),wxyz=ast.literal_eval(first.args[1]))
            cfg=np.concatenate([primary['joint_positions'],[0.]]).astype(np.float32);checks=[]
            for pose in [above,target]:
                check=solve_pose_candidate(pose['xyz'],pose['wxyz'],cfg,[0,0,-.107],solver,fk,lambda:None)
                checks.append(check)
                if check['status']!='accepted':break
                cfg=np.asarray(check['configuration'])
            entry['kinematics']=checks
            for name,mount in [('robot0_robotview','fixed'),('agentview','fixed'),('frontview','fixed'),('robot0_eye_in_hand','tcp_mounted')]:
                high=packet(folder/f'view-high-{name}.npz')
                prediction=predict_obstruction(high,current,target,'red cube','green cube',mount=mount)
                actual=row['held_low']['views'][name].get('support_evidence',{})
                entry['views'].append(dict(camera=name,prediction=prediction,
                    observed_low=dict(status=actual.get('status'),reason=actual.get('reason'),ratio=actual.get('horizontal_span_ratio'),evidence_ref=actual.get('evidence_ref'))))
        else:
            episode_segments=list(episode['segments'].values());rotation=next(s for s in episode_segments if s['subgoal']=='support-yaw')
            state=next(x['result'] for x in reversed([json.loads(l) for l in (folder/'events.jsonl').read_text().splitlines() if l]) if x['kind']=='api_result' and x.get('name')=='get_execution_state')
            action=next(x for x in rotation['api_attempts'] if x['name']=='goto_pose')
            initial=np.concatenate([state['joint_positions'],[0.]]).astype(np.float32)
            check=solve_pose_candidate(action['args'][0],action['args'][1],initial,[0,0,-.107],solver,fk,lambda:None)
            entry['kinematics']=[check];entry['actual_ik_rejection']=(rotation.get('feedback')or{}).get('evidence',{}).get('reason')
        results.append(entry)
        (a.output/'results.json').write_text(json.dumps(dict(scope='Experimental historical public-state preview; zero actions and no performance claim',cases=results),indent=2))
        print(json.dumps(dict(seed=row['seed'],yaw=row['support_yaw_degrees'],kinematics=[x['status'] for x in entry.get('kinematics',[])],views=[dict(camera=v['camera'],predicted=v['prediction'].get('predicted_obstructed_fraction'),remaining_ratio=v['prediction'].get('predicted_remaining_horizontal_ratio'),actual=v['observed_low']['status']) for v in entry['views']])),flush=True)


if __name__=='__main__':main()
