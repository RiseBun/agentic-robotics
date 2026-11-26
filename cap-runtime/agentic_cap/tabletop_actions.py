"""Bounded tabletop tools. Programs never receive devices, arrays or trajectories."""
import copy
import time
import uuid
import numpy as np
from .tabletop_domain import SKILLS, effects_for
from .tabletop_sensors import project


def tools(backend,segment):
    b=backend;e=b.episode
    binding=segment.get('task_binding') or {}
    node=next((n for n in e.observe()['system']['task']['nodes'] if n['id']==binding.get('node_id')),None)
    if node is None:raise ValueError('Missing current task binding')
    allowed=node['skills'][0];args=node['arguments']
    def check(name,obj):
        e.checkpoint(segment)
        if name!=allowed or obj!=args['object']:raise ValueError('Call outside admitted operation/object binding')
    def grasp(obj):
        check('acquire_object',obj)
        if b.verify({'all':[dict(predicate='empty_gripper')]},e.observe())['status']!='verified':
            raise ValueError('Fresh empty-gripper evidence is required before grasp planning')
        from cap_harness.contracts import PointCloud
        from cap_harness.providers.graspnet.client import GraspNetProvider
        packet,objects,o=b._geometry_packet(obj,segment);cam=packet['cameras'][o['camera']]
        points,_=project(cam,objects[obj]['mask']);scene,_=project(cam)
        # Contact-GraspNet is trained in camera coordinates. Transform public
        # points into that frame; transform returned poses back to world.
        T=np.array(cam['camera_to_world']);R=T[:3,:3];t=T[:3,3]
        points=(points-t)@R;scene=(scene-t)@R
        provider=GraspNetProvider('http://127.0.0.1:8115',timeout_s=30,max_retries=0)
        result=provider.generate_grasps(PointCloud(points,'camera'),scene_point_cloud=PointCloud(scene[::4],'camera'),max_candidates=8)
        e.checkpoint(segment)
        candidates=sorted(result.grasps,key=lambda c:c.score,reverse=True) if result.ok else []
        e.event('grasp_candidates_generated',object=obj,provider='contact_graspnet',count=len(candidates),error=str(result.error) if not result.ok else None)
        from scipy.spatial.transform import Rotation
        options=[]
        for chosen in candidates:
            center=R@np.array(chosen.pose.position)+t
            cq=np.array(chosen.pose.quaternion_wxyz);rot=R@Rotation.from_quat(cq[[1,2,3,0]]).as_matrix()
            # Contact-GraspNet predicts its gripper base, not the Panda TCP.
            # This adapter calibration matches the existing CaP-X Franka bridge.
            center=center+rot@np.array([0.,0.,.12])
            quat=Rotation.from_matrix(rot).as_quat()[[3,0,1,2]]
            if rot[2,2]>-.4:continue
            options.append(dict(approach=(center+[0,0,.12],quat),grasp=(center,quat),lift=(center+[0,0,.15],quat)))
        # Generic measured-geometry candidates for upright rigid primitives.
        # These are S1-generated candidates, not per-task programs or LLM coordinates.
        ref=b.reference.get(obj)
        if ref and o.get('status')=='available':
            lo=np.array(o['surface_q02_xyz']);hi=np.array(o['surface_q98_xyz'])
            center=np.r_[(lo[:2]+hi[:2])/2,hi[2]-ref['observed_height_m']/2+.005]
            geometric=[]
            for dz in [0.,.012]:
                for quat in [np.array([0.,0.,1.,0.]),np.array([0.,1.,0.,0.])]:
                    target=center+np.array([0,0,dz])
                    geometric.append(dict(approach=(target+[0,0,.12],quat),grasp=(target,quat),lift=(target+[0,0,.15],quat)))
            version=b.object_versions.get(obj)
            history=b.grasp_attempts.get(obj,{})
            attempt=history.get('count',0) if history.get('version')==version else 0
            b.grasp_attempts[obj]=dict(version=version,count=attempt+1)
            if attempt>=len(geometric):raise ValueError('Distinct bounded geometric grasp candidates exhausted; acquire new evidence or request help')
            options=geometric[attempt:]+options
            e.event('grasp_candidates_generated',object=obj,provider='measured_upright_primitive_geometry',count=2,evidence_ref=e.snapshot.get('evidence_ref'))
        if not options:raise ValueError('No grasp candidate matches the supported overhead approach envelope')
        ident=uuid.uuid4().hex
        b.plans[ident]=dict(segment=segment['id'],revision=e.revision,kind='grasp',object=obj,
            targets=options[0],alternatives=options[:3],
            sequence=['open','approach','grasp','closed','lift'],cursor=0,exclude=obj,object_version=b.object_versions.get(obj))
        b.last_plan=ident
        return ident
    def place(obj,support=None,region=None):
        check('place_in_region' if region is not None else 'place_object',obj)
        if support!=args.get('support') or region!=args.get('region'):raise ValueError('Destination differs from admitted node')
        s=e.observe()
        if b.verify({'all':[dict(predicate='holding',object=obj)]},s)['status']!='verified':
            raise ValueError('Current holding evidence is required')
        _,_,o=b._geometry_packet(obj,segment);tcp=np.array(s['tcp_xyz']);quat=np.array(s['tcp_wxyz'])
        ref=b.reference.get(obj,{})
        if 'observed_height_m' not in ref:raise ValueError('Observed object height is unknown')
        half=ref['observed_height_m']/2
        olo=np.array(o['surface_q02_xyz']);ohi=np.array(o['surface_q98_xyz'])
        center=np.r_[(olo[:2]+ohi[:2])/2,ohi[2]-half];offset=center-tcp
        if region:
            from .tabletop_scene import public_catalog
            r=public_catalog()['regions'][region];lo,hi=np.array(r['bounds_xy']);target=np.r_[(lo+hi)/2,r['surface_z']+half+.003]-offset
        else:
            _,_,sup=b._geometry_packet(support,segment)
            from .tabletop_sensors import support_surface
            from .tabletop_scene import public_catalog
            if public_catalog()['objects'][support]['shape']=='box':
                sup=support_surface(sup,b.reference.get(support))
                if sup is None:raise ValueError('Current support extent cannot be reconstructed from public evidence')
            lo=np.array(sup['surface_q02_xyz']);hi=np.array(sup['surface_q98_xyz'])
            width=np.array(o['surface_span_xyz'])[:2]
            if np.any(hi[:2]-lo[:2]<.025):raise ValueError('Observed support cannot accommodate the required center margin')
            target=np.r_[(lo[:2]+hi[:2])/2,hi[2]+half+.003]-offset
        ident=uuid.uuid4().hex
        b.plans[ident]=dict(segment=segment['id'],revision=e.revision,kind='place',object=obj,support=support,region=region,
            targets=dict(approach=(target+[0,0,.13],quat),place=(target,quat),retreat=(target+[0,0,.13],quat)),
            sequence=['approach','place','open','retreat'],cursor=0,exclude=obj)
        return ident
    def stage(ident,stage):
        e.checkpoint(segment);p=b.plans.get(ident)
        if not p or p['segment']!=segment['id'] or p['revision']!=e.revision:raise ValueError('Unknown or stale trajectory handle')
        if p['cursor']>=len(p['sequence']) or p['sequence'][p['cursor']]!=stage:raise ValueError('Invalid operation stage order')
        if p['kind']=='grasp' and stage in {'approach','grasp'} and p.get('object_version')!=b.object_versions.get(p['object']):
            raise ValueError('Target object moved after grasp candidate generation')
        if p['kind']=='place' and stage!='retreat' and b.verify({'all':[dict(predicate='holding',object=p['object'])]},e.observe())['status']!='verified':
            raise ValueError('Unknown current custody forbids another transport stage')
        options=p.get('alternatives',[p['targets']]) if stage=='approach' else [p['targets']]
        trajectory=None;errors=[]
        for option in options:
            xyz,quat=option[stage]
            try:
                trajectory=b._plan_pose(segment,xyz,quat,exclude=p['exclude']);p['targets']=option;break
            except ValueError as exc:
                errors.append(str(exc));e.event('grasp_candidate_rejected',reason=str(exc),object=p['object'])
        if trajectory is None:raise ValueError('No candidate path: '+'; '.join(errors))
        b.restoration_done=None
        result=b._move_trajectory(segment,trajectory);p['cursor']+=1;b.last_plan=ident
        if p['kind']=='place' and stage=='place':
            snap=e.observe();b.recorded_descent=dict(object=p['object'],trajectory=trajectory,revision=e.revision,
                other_versions={k:v for k,v in snap.get('object_versions',{}).items() if k!=p['object']},
                evidence_ref=snap.get('evidence_ref'))
        return result
    def gripper(command):
        e.checkpoint(segment);p=b.plans.get(b.last_plan)
        if not p or p['segment']!=segment['id'] or p['sequence'][p['cursor']]!=command:raise ValueError('No admitted gripper stage')
        if command=='open' and p['kind']=='grasp':
            if b.verify({'all':[dict(predicate='empty_gripper')]},e.observe())['status']!='verified':
                raise ValueError('Grasp preparation may only open an observed empty gripper')
        if command=='open' and p['kind']=='place':
            # A transported surface is not a stable release observation. Wait
            # for the public post-motion window while S0 keeps holding.
            with e.cv:
                start=b.tick
                while b.tick-start<20:e.checkpoint(segment);e.cv.wait(.03)
            if p.get('support'):
                _,_,support=b._geometry_packet(p['support'],segment)
            release_conditions=[dict(predicate='holding',object=p['object'])]
            if p.get('support'):release_conditions.append(dict(predicate='support_geometry',object=p['support']))
            if p.get('region'):release_conditions.append(dict(predicate='region',region=p['region']))
            admission=b.wait_public_effect(segment,{'all':release_conditions})
            e.event('release_admission_checked',object=p['object'],result=admission,
                evidence_ref=e.snapshot.get('evidence_ref'))
            if admission['status']!='verified':
                raise ValueError('Unknown holding or destination evidence forbids release')
        with e.cv:
            e.checkpoint(segment);e.gripper=1. if command=='open' else 0.;e.motion=True;start=b.tick
            while b.tick-start<12:e.checkpoint(segment);e.cv.wait(.03)
            e.motion=False
        p['cursor']+=1;b.last_action_tick=b.tick
        if command=='closed':b.held_hint=p['object']
        elif p['kind']=='place':b.held_hint=None;b.recorded_descent=None
        return dict(status='verified',scope='gripper_command_only')
    def inspect(obj,evidence="geometry"):
        check('inspect_object',obj)
        if evidence!=args.get('evidence','geometry'):raise ValueError('Evidence target differs from the admitted inspection')
        desired={'all':[dict(predicate=evidence,object=obj)]}
        before=e.observe();start=time.monotonic();used=e.used.copy();views=0
        held=b.held_hint
        visited=b.inspection_sessions.setdefault(obj,set())
        e.event('evidence_acquisition_started',object=obj,before_evidence_ref=before.get('evidence_ref'))
        result=b.verify(desired,before)
        if result['status']!='verified':
            # Ask the perception worker to renew semantic identity on a fresh
            # frame before spending a physical view change.
            if hasattr(b,'reacquire'):
                b.reacquire.put(obj)
                with e.cv:
                    end=time.monotonic()+3
                    while time.monotonic()<end:
                        e.checkpoint(segment);current=e.observe()
                        result=b.verify(desired,current)
                        if result['status']=='verified':break
                        e.cv.wait(.05)
        if result['status']!='verified':
            # Finite, calibrated camera candidates. A fixed camera cannot be
            # moved by declaring a new view; only the wrist pose may change.
            initial=np.array(before['tcp_xyz']);quat=np.array(before['tcp_wxyz'])
            candidates=[dict(target_xyz=(initial+[0,dy,0]).tolist()) for dy in [-.08,.08]]
            coverage=getattr(b,'inspection_policy','lateral')=='coverage'
            if coverage:
                from .tabletop_views import rank_views,signature as view_signature
                empty=b.verify({'all':[dict(predicate='empty_gripper')]},e.observe())['status']=='verified'
                # Translation-only broad candidates require observed empty grip;
                # unknown custody never authorizes an exploratory camera motion.
                cannot_move=not held and not empty
                key=(e.robot_system.goal_version,obj)
                anchor=b.inspection_anchors.setdefault(key,initial.copy())
                reference=b.reference.get(obj,{})
                from .tabletop_scene import public_catalog
                prior=reference.get('surface_median_xyz') if not public_catalog()['objects'][obj].get('movable',False) else b.baselines.get(obj)
                if cannot_move:
                    candidates=[];result=dict(status='unknown',reason='empty_gripper_not_verified_for_view_selection')
                elif prior is None:
                    candidates=[];result=dict(status='unknown',reason='no_public_aiming_prior')
                elif held and anchor[2]<.95:
                    candidates=[];result=dict(status='unknown',reason='low_held_pose_requires_recorded_restore_before_inspection')
                else:
                    packet=b.raw
                    candidates=rank_views(packet,anchor,prior,reference.get('surface_span_xyz',[.04,.04,.04]),visited,holding=bool(held))
                    e.event('inspection_selection_inputs',object=obj,aim_xyz=np.asarray(prior).tolist(),
                        measured_span=reference.get('surface_span_xyz'),
                        source_captured_at=packet.get('captured_at'),selection_time=time.time(),
                        source_robot_xyz=np.asarray(packet.get('tcp_xyz')).tolist(),
                        camera_times={k:v.get('captured_at') for k,v in packet['cameras'].items()},
                        visited=[list(v) for v in visited])
                    if not candidates:result=dict(status='unknown',reason='no_admissible_projected_view; change_evidence_condition_or_ask_user')
                e.event('inspection_candidates_ranked',object=obj,evidence=evidence,candidates=candidates,
                    aim_source='prior_public_measurement_not_current_fact',anchor_xyz=anchor.tolist())
            for candidate in candidates[:2]:
                e.checkpoint(segment)
                if held and b.verify({'all':[dict(predicate='holding',object=held)]},e.observe())['status']!='verified':
                    result=dict(status='unknown',reason='unknown_custody_forbids_camera_motion');break
                target=np.array(candidate['target_xyz'])
                signature=view_signature(target) if coverage else tuple(np.round(target,3))
                if signature in visited:continue
                visited.add(signature);views+=1
                e.event('view_attempt',object=obj,view_index=views,before_evidence_ref=e.snapshot.get('evidence_ref'),target_xyz=target.tolist(),camera='wrist',view_policy='coverage' if coverage else 'lateral',prediction=candidate)
                try:trajectory=b._plan_pose(segment,target,quat)
                except ValueError as exc:
                    if not coverage:raise
                    e.event('inspection_candidate_rejected',object=obj,target_xyz=target.tolist(),reason=str(exc))
                    continue
                b._move_trajectory(segment,trajectory)
                with e.cv:
                    start_tick=b.tick
                    while b.tick-start_tick<20:e.checkpoint(segment);e.cv.wait(.03)
                current=e.observe()
                if held and b.verify({'all':[dict(predicate='holding',object=held)]},current)['status']!='verified':
                    result=dict(status='unknown',reason='holding_not_verified_after_inspection');break
                result=b.verify(desired,current)
                if result['status']=='verified':break
        out=dict(status='available' if result['status']=='verified' else 'unknown',views_used=views,object=obj,
            evidence_ref=e.snapshot.get('evidence_ref'),reason=result.get('reason','public_observation'),
            motion_steps=e.used['motion_steps']-used['motion_steps'],api_calls=e.used['calls']-used['calls'],wall_seconds=time.monotonic()-start)
        e.event('evidence_acquisition_finished',**out);return out
    def restore(obj):
        check('restore_grip_pose',obj)
        condition={'all':[dict(predicate='recorded_descent',object=obj)]}
        if b.verify(condition,e.observe())['status']!='verified':raise ValueError('No current recorded descent may be reversed')
        from dataclasses import replace
        record=b.recorded_descent;state,_=b._typed_state(e.observe());original=record['trajectory']
        reverse=replace(original,joint_positions=np.array(original.joint_positions)[::-1].copy(),expected_start=state)
        e.event('recorded_restore_started',object=obj,source_evidence_ref=record['evidence_ref'])
        b._move_trajectory(segment,reverse);b.recorded_descent=None
        b.restoration_done=dict(object=obj,revision=e.revision,joints=b.encoder.tolist())
        with e.cv:
            start=b.tick
            while b.tick-start<20:e.checkpoint(segment);e.cv.wait(.03)
        result=b.wait_public_effect(segment,{'all':[dict(predicate='holding',object=obj)]})
        e.event('recorded_restore_finished',object=obj,result=result)
        return result

    def verify_effect(goal):
        expected={'all':effects_for(allowed,args)}
        if goal!=expected:raise ValueError('Cannot replace Runtime skill verification')
        with e.cv:
            start=b.tick
            while b.tick-start<20:e.checkpoint(segment);e.cv.wait(.03)
        result=b.wait_public_effect(segment,expected)
        if allowed=='acquire_object' and result['status']=='verified':b.holding_watch=dict(object=args['object'],failures=0)
        return result
    return dict(plan_grasp=grasp,plan_place=place,execute_stage=stage,set_gripper=gripper,
                inspect_object=inspect,restore_grip_pose=restore,verify_effect=verify_effect)
