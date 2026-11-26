"""Public existing-camera diagnosis. No LLM, camera relocation or truth inputs."""
import argparse,copy,hashlib,json,os,math
from agentic_cap.skills import compile_skill,rotation_clearance
from pathlib import Path
from agentic_cap.core import Manager
from experiments.release_camera_probe import ProbeBackend,request_probe
from experiments.active_view_trial import wait_for
from experiments.vertical_retreat_trial import source_hashes


def stage(e,label,kind,skill=None,code=None):
    state=e.observe()['system'];e.decision(label,e.revision)
    if code:
        sid=e.execute(label,e.revision,label,code,contract=dict(postcondition=dict(holding='red cube')) if label=='place' else None,task_ref=dict(state_id=state['state_id'],task_node='1:'+kind))
    else:
        sid=e.execute_skill(label,e.revision,state['state_id'],'1:'+kind,skill)
    wait_for(lambda:e.active is None,90)
    result=copy.deepcopy(e.segments[sid])
    print(json.dumps(dict(seed=e.seed,stage=label,execution=result['execution'],error=result.get('error'))),flush=True)
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--records',type=Path,required=True)
    p.add_argument('--upstream',required=True);p.add_argument('--project',type=Path,required=True)
    a=p.parse_args();root=a.records.resolve();root.mkdir(parents=True,exist_ok=False)
    os.environ.update(CAP_ACTIVE_EVIDENCE='1',CAP_PLACEMENT_EVIDENCE='1',CAP_HOLDING_WATCHDOG='1',CAP_SUPPORT_SECOND_VIEW='robot0_eye_in_hand',CAP_ALTERNATE_PLACEMENT='0',CAP_SUPPORT_OCCLUSION='')
    sources=source_hashes();plan=[]
    for seed in [22,12]:
        prior=a.project/f'.tmp/continuation-resources-v2-20261122/cube_restack-s{seed}'
        data=json.loads(next(prior.glob('physical/*/episode.json')).read_text())
        clear=next(s for s in data['segments'].values() if (s.get('task_binding')or{}).get('node_id')=='1:clear')
        for yaw in [0,45]:
            plan.append(dict(seed=seed,support_yaw_degrees=yaw,clearing_code=clear['code'],source_episode=data['id'],source_code_sha256=hashlib.sha256(clear['code'].encode()).hexdigest()))
    (root/'protocol.json').write_text(json.dumps(dict(plan=plan,sources=sources,scope='2x2 development diagnosis: support orientation0/45 and seeds22/12; no target release; public cameras only; no LLM/private feedback'),indent=2))
    rows=[]
    for item in plan:
        assert source_hashes()==sources
        manager=Manager(root/f'seed{item["seed"]}-yaw{item["support_yaw_degrees"]}',lambda e:ProbeBackend(e,a.upstream,evaluation=True))
        state=manager.create(f'audit-{item["seed"]}-yaw{item["support_yaw_degrees"]}','cube_restack',item['seed'],dict(on=['red cube','green cube'],empty_gripper=True))
        e=manager.episodes[state['id']];row=dict(seed=item['seed'],support_yaw_degrees=item['support_yaw_degrees'],episode_id=e.id,status='failed')
        try:
            wait_for(lambda:e.snapshot or e.status=='failed',150)
            if e.status!='ready':raise RuntimeError('Backend initialization failed')
            row['initial']=request_probe(e,'initial')
            wait_for(lambda:e.observe()['system']['world']['facts']['on:green cube:red cube']['status']=='verified',5)
            first=stage(e,'blocker','clear_acquire',dict(name='acquire_object',arguments=dict(object='green cube')))
            if first['execution']!='completed':raise RuntimeError('Blocker failed')
            if item['support_yaw_degrees']:
                snapshot=e.observe()
                if not rotation_clearance(snapshot,'green cube'):raise RuntimeError('Public high-clearance envelope unavailable')
                q=snapshot['tcp_wxyz'];length=math.sqrt(sum(v*v for v in q));w,x,y,z=[v/length for v in q]
                angle=math.radians(item['support_yaw_degrees'])/2;c,s=math.cos(angle),math.sin(angle)
                quat=[c*w-s*z,c*x-s*y,c*y+s*x,c*z+s*w]
                source=f's=get_execution_state()\ngoto_pose(s["tcp_xyz"],{quat!r})\ncheck_holding("green cube")'
                rotated=stage(e,'support-yaw','clear',code=source)
                if rotated['execution']!='completed':raise RuntimeError('Support orientation failed')
            clear=stage(e,'clear','clear',code=item['clearing_code'])
            if clear['execution']!='completed':raise RuntimeError('Clearing failed')
            target=stage(e,'target','acquire',dict(name='acquire_object',arguments=dict(object='red cube')))
            if target['execution']!='completed':raise RuntimeError('Target acquisition failed')
            row['held_high']=request_probe(e,'high')
            snapshot=e.observe();system=snapshot['system']
            program,_=compile_skill(dict(name='place_object',arguments=dict(object='red cube',support='green cube')),system,snapshot)
            if program.count('open_gripper()\n')!=1:raise RuntimeError('Unrecognized registered place program')
            prefix=program.split('open_gripper()\n')[0]
            row['pre_release_program']=prefix
            placed=stage(e,'place','place',code=prefix)
            if placed['execution']!='completed':raise RuntimeError('Pre-release descent failed')
            row['placement']=dict(execution=placed['execution'],feedback=placed.get('feedback'),target_released=False)
            row['held_low']=request_probe(e,'low');row['gripper_command']=e.gripper;row['status']='captured'
        except Exception as exc:row['error']=str(exc)
        finally:
            e.close();row.update(closed=e.state()['status'],used=e.state()['used'],source_unchanged=source_hashes()==sources)
            rows.append(row);(root/'results.json').write_text(json.dumps(rows,indent=2))
        print(json.dumps(dict(seed=row['seed'],support_yaw_degrees=row['support_yaw_degrees'],status=row['status'],error=row.get('error'),low_views={n:v.get('support_evidence') for n,v in row.get('held_low',{}).get('views',{}).items()})),flush=True)
    if not all(row['status']=='captured' and row['closed']=='closed' and row['source_unchanged'] for row in rows):raise SystemExit(1)


if __name__=='__main__':main()
