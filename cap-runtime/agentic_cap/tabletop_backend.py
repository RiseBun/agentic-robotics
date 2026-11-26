"""Continuous tabletop backend with asynchronous perception and typed providers.

Only initialize/step/capture/close touch simulator state, on the physics owner.
Policy tools consume copied public packets and provider-issued trajectory handles.
"""
import copy
import json
import os
import queue
import threading
import time
import uuid
from collections import deque
import numpy as np
from scipy.spatial.transform import Rotation
from .core import Halt, plain
from .tabletop_scene import public_catalog
from .tabletop_sensors import project, geometry, verify


class TabletopBackend:
    period=.05
    def __init__(self,episode,evaluation=False):
        self.episode=episode;self.evaluation=evaluation;self.tick=0;self.owner=None;self.env=None
        self.encoder=np.zeros(7);self.latest=None;self.history=deque(maxlen=24);self.reference={}
        self.reacquire=queue.SimpleQueue();self.processing=queue.Queue(maxsize=1);self.closing=False;self.raw=None;self.plans={}
        self.restoration_done=None;self.recorded_descent=None;self.grasp_attempts={};self.object_versions={k:0 for k in public_catalog()['objects']};self.baselines={}
        self.multiview=os.environ.get('TABLETOP_MULTIVIEW','0')=='1';self.perception_error=None;self.planner_verified=False;self.last_action_tick=0
        self.base_T=np.eye(4);self.last_plan=None;self.held_hint=None;self.appearance={};self.inspection_sessions={};self.holding_watch=None

    def initialize(self):
        from robosuite.controllers import load_composite_controller_config,load_part_controller_config
        from .tabletop_simulation import GenericTabletop
        self.owner=threading.get_ident()
        controller=load_composite_controller_config(controller='BASIC')
        part=load_part_controller_config(default_controller='JOINT_POSITION')
        part.update(input_type='absolute',input_min=-10,input_max=10,output_min=-10,output_max=10,kp=150)
        controller['body_parts']['right']=dict(part,gripper=dict(type='GRIP'))
        scene=(self.episode.creation or {}).get('payload',{}).get('scene','tabletop_v1')
        self.env=GenericTabletop(robots='Panda',controller_configs=controller,has_renderer=False,
            has_offscreen_renderer=True,use_camera_obs=False,use_object_obs=False,
            control_freq=20,horizon=100000,ignore_done=True,hard_reset=False,seed=self.episode.seed,
            scene=scene,table_full_size=(.8,.8,.05))
        self.env.reset();self.encoder=self.env.robots[0]._joint_positions.copy()
        # Robot mounting calibration, not object ground truth.
        base_id=self.env.sim.model.body_name2id('robot0_link0')
        self.base_T[:3,3]=self.env.sim.data.body_xpos[base_id]
        self.base_T[:3,:3]=np.array(self.env.sim.data.body_xmat[base_id]).reshape(3,3)
        if not np.allclose(self.base_T[:3,:3],np.eye(3),atol=1e-6):
            raise ValueError('This Panda bridge requires the calibrated unrotated base mount')
        self.episode.event('robot_mount_calibrated',base_transform=self.base_T.tolist(),source='robot_mount_configuration')
        self.started_sim=float(self.env.sim.data.time)
        thread=threading.Thread(target=self._process,name='tabletop-perception',daemon=True);thread.start();self.worker=thread

    def joints(self):
        assert threading.get_ident()==self.owner
        return self.encoder.tolist()

    def step(self,target,gripper):
        assert threading.get_ident()==self.owner
        self.env.step(np.r_[target,1-2*gripper],skip_render_images=True)
        self.encoder=self.env.robots[0]._joint_positions.copy();self.tick+=1

    def sim_time(self):return float(self.env.sim.data.time)-self.started_sim

    def capture(self):
        assert threading.get_ident()==self.owner
        from robosuite.utils.camera_utils import get_real_depth_map,get_camera_intrinsic_matrix,get_camera_extrinsic_matrix
        from PIL import Image
        packets={}
        names={'overview':'agentview','wrist':'robot0_eye_in_hand'}
        for ident,name in names.items():
            rgb,depth=self.env.sim.render(camera_name=name,width=320,height=240,depth=True)
            rgb=np.array(rgb[::-1],copy=True);depth=get_real_depth_map(self.env.sim,depth[::-1])
            packets[ident]=dict(rgb=rgb,depth=depth,intrinsics=get_camera_intrinsic_matrix(self.env.sim,name,240,320),
                camera_to_world=get_camera_extrinsic_matrix(self.env.sim,name),captured_at=time.time(),name=ident)
        robot=self.env.robots[0];site=robot.eef_site_id['right'];sim=self.env.sim
        pos=np.array(sim.data.site_xpos[site],copy=True)
        # Panda grip-site axes differ from panda_hand. The planner expects
        # hand orientation with the calibrated grip-site position.
        hand=sim.model.body_name2id(robot.robot_model.eef_name['right'])
        rot=np.array(sim.data.body_xmat[hand]).reshape(3,3)
        quat=Rotation.from_matrix(rot).as_quat()[[3,0,1,2]]
        fingers=np.array(sim.data.qpos[robot._ref_gripper_joint_pos_indexes['right']],copy=True)
        packet=dict(cameras=packets,joint_positions=self.encoder.copy(),tcp_xyz=pos,tcp_wxyz=quat,
                    gripper_joint_positions=fingers,captured_at=time.time(),control_step=self.tick,
                    last_gripper_command='closed' if self.episode.gripper<.5 else 'open',simulation_time=self.sim_time())
        self.raw=packet
        try:self.processing.put_nowait(packet)
        except queue.Full:pass
        # Camera snapshots stay independent of slow provider calls.
        name=f'frame-{self.tick:06d}'
        file=self.episode.directory/(name+'.png');Image.fromarray(packets['overview']['rgb']).save(file)
        snapshot=copy.deepcopy(self.latest) if self.latest else dict(objects={},object_versions=self.object_versions.copy())
        snapshot.update({k:plain(v) for k,v in packet.items() if k!='cameras'})
        snapshot.update(evidence_ref=name+'.npz',image_ref=name+'.png',camera_name='agentview',backend='tabletop',catalog=public_catalog(),
            coordinate_convention=dict(frame='world',length_unit='meter',quaternion_order='wxyz'),
            perception_error=self.perception_error,capabilities=['inspect_object'],planning_scope='public_point_cloud')
        # Record actual public camera packets, never object poses or contact labels.
        with (self.episode.directory/(name+'.npz')).open('wb') as stream:
            np.savez_compressed(stream,rgb=packets['overview']['rgb'],depth=packets['overview']['depth'].astype(np.float32),
                intrinsics=packets['overview']['intrinsics'],camera_to_world=packets['overview']['camera_to_world'],
                joint_positions=self.encoder,control_step=self.tick,captured_at=packet['captured_at'])
        wrist_name=f'view-robot0_eye_in_hand-{self.tick:06d}'
        Image.fromarray(packets['wrist']['rgb']).save(self.episode.directory/(wrist_name+'.png'))
        with (self.episode.directory/(wrist_name+'.npz')).open('wb') as stream:
            np.savez_compressed(stream,rgb=packets['wrist']['rgb'],depth=packets['wrist']['depth'].astype(np.float32),
                intrinsics=packets['wrist']['intrinsics'],camera_to_world=packets['wrist']['camera_to_world'])
        snapshot['sensors']={key:dict(intrinsics=cam['intrinsics'].tolist(),camera_to_world=cam['camera_to_world'].tolist(),captured_at=cam['captured_at'],image_size=[320,240]) for key,cam in packets.items()}
        snapshot['camera_views']={'robot0_eye_in_hand':dict(camera_name='robot0_eye_in_hand',episode_id=self.episode.id,
            captured_at=packet['captured_at'],control_step=self.tick,coordinate_convention=snapshot['coordinate_convention'],
            image_ref=wrist_name+'.png',evidence_ref=wrist_name+'.npz')}
        self.history.append(copy.deepcopy(snapshot))
        if self.evaluation:self._private_score()
        return snapshot

    def _process(self):
        from cap_harness.contracts import CameraObservation,Pose
        from cap_harness.providers.sam3.client import Sam3Provider
        provider=Sam3Provider(os.environ.get('TABLETOP_SAM_URL','http://127.0.0.1:8114'),timeout_s=15,max_retries=0)
        while not self.closing:
            try:packet=self.processing.get(timeout=.5)
            except queue.Empty:continue
            objects={}
            while not self.reacquire.empty():
                self.appearance.pop(self.reacquire.get_nowait(),None)
            try:
                cam=packet['cameras']['overview'];T=cam['camera_to_world'];q=Rotation.from_matrix(T[:3,:3]).as_quat()[[3,0,1,2]]
                obs=CameraObservation(cam['rgb'],cam['depth'],cam['intrinsics'],'overview',Pose(T[:3,3],q,'world'),cam['captured_at'])
                for ident,d in public_catalog()['objects'].items():
                    if ident=='table':continue
                    if ident not in self.appearance:
                        result=provider.segment_text(obs,d['label'])
                        if not result.ok or not result.segmentations:
                            objects[ident]=dict(status='unknown',reason='segmentation_unavailable');continue
                        best=max(result.segmentations,key=lambda s:s.score)
                        pixels=cam['rgb'][best.mask].astype(float)
                        if len(pixels)<24:
                            objects[ident]=dict(status='unknown',reason='insufficient_identity_pixels');continue
                        chroma=pixels/np.maximum(pixels.sum(axis=1,keepdims=True),1)
                        self.appearance[ident]=dict(chroma=np.median(chroma,axis=0),score=float(best.score),identity_observed_at=cam['captured_at'])
                        mask=best.mask
                    else:
                        # SAM establishes semantic identity. Subsequent facts use
                        # CURRENT RGB-D pixels, never a timestamp refreshed on an
                        # old mask/geometry. Ambiguous appearance stays unknown.
                        rgb=cam['rgb'].astype(float);chroma=rgb/np.maximum(rgb.sum(axis=2,keepdims=True),1)
                        target=self.appearance[ident]['chroma']
                        distances={key:np.linalg.norm(chroma-v['chroma'],axis=2) for key,v in self.appearance.items()}
                        dist=distances[ident];mask=dist<.10
                        for key,other in distances.items():
                            if key!=ident:mask &= dist+.025<other
                        depth=cam['depth'];yy,xx=np.indices(depth.shape);K=cam['intrinsics'];T=cam['camera_to_world']
                        local=np.stack([(xx-K[0,2])*depth/K[0,0],(yy-K[1,2])*depth/K[1,1],depth],axis=-1)
                        world=local@T[:3,:3].T+T[:3,3]
                        mask &= (depth>.05)&(world[:,:,2]>.805)&(world[:,:,2]<1.4)&(abs(world[:,:,0])<.38)&(abs(world[:,:,1])<.38)
                        prior=self.baselines.get(ident)
                        if prior is not None:
                            radius=.13 if not d.get('movable',False) else .22
                            mask &= np.linalg.norm(world-prior,axis=2)<radius
                        import cv2
                        count,labels,stats,centers=cv2.connectedComponentsWithStats(mask.astype(np.uint8),8)
                        if count<2:
                            if not self.multiview:
                                objects[ident]=dict(status='unknown',reason='appearance_track_lost');continue
                            mask=np.zeros(mask.shape,dtype=bool)
                        else:
                            largest=1+int(np.argmax(stats[1:,cv2.CC_STAT_AREA]));mask=labels==largest
                    if self.multiview:
                        from .tabletop_multiview import measure_object
                        objects[ident]=measure_object(packet,ident,self.appearance,self.baselines.get(ident),
                            self.reference.get(ident),d.get('movable',False),initial_mask=mask)
                        mask=objects[ident].get('mask')
                    else:
                        objects[ident]=geometry(cam,mask)
                        objects[ident].update(source='sam3_identity_current_rgbd_tracking',camera='overview',
                            identity_observed_at=self.appearance[ident]['identity_observed_at'],
                            segmentation_score=self.appearance[ident]['score'])
                        if objects[ident]['status']!='available' and ident in self.appearance:
                            wrist=packet['cameras']['wrist'];rgb=wrist['rgb'].astype(float)
                            chroma=rgb/np.maximum(rgb.sum(axis=2,keepdims=True),1)
                            dist=np.linalg.norm(chroma-self.appearance[ident]['chroma'],axis=2);alternate=dist<.10
                            for key,value in self.appearance.items():
                                if key!=ident:alternate &= dist+.025<np.linalg.norm(chroma-value['chroma'],axis=2)
                            other=geometry(wrist,alternate)
                            if other['status']=='available':
                                objects[ident]=dict(other,source='sam3_identity_current_rgbd_tracking',camera='wrist',identity_observed_at=self.appearance[ident]['identity_observed_at'])
                                mask=alternate
                    if objects[ident]['status']=='available':
                        o=objects[ident];center=np.array(o['surface_median_xyz']);prior=self.baselines.get(ident)
                        if prior is not None and np.linalg.norm(center-prior)>.02:self.object_versions[ident]+=1
                        if prior is None or np.linalg.norm(center-prior)>.02:self.baselines[ident]=center
                        # Only establish a height reference when the visible point
                        # cloud reaches the calibrated table. Stacked/occluded
                        # objects do not silently acquire inferred dimensions.
                        lo=o['surface_q02_xyz'];hi=o['surface_q98_xyz']
                        if ident not in self.reference and lo[2]<.82 and hi[2]>.815:
                            self.reference[ident]=dict(o,observed_height_m=hi[2]-.8)
                        o['mask']=mask
                for ident,o in objects.items():
                    if ident in self.reference or o.get('status')!='available':continue
                    lo=np.array(o['surface_q02_xyz']);hi=np.array(o['surface_q98_xyz']);center=(lo[:2]+hi[:2])/2
                    supports=[]
                    for other,base in objects.items():
                        if other==ident or base.get('status')!='available':continue
                        blo=np.array(base['surface_q02_xyz']);bhi=np.array(base['surface_q98_xyz'])
                        if np.all(center>=blo[:2]-.01) and np.all(center<=bhi[:2]+.01) and abs(lo[2]-bhi[2])<.012 and hi[2]-bhi[2]>.02:
                            supports.append((other,bhi[2]))
                    if len(supports)==1:
                        support,z=supports[0]
                        self.reference[ident]=dict(o,observed_height_m=float(hi[2]-z),height_reference='observed_support_surface',support=support)
                table_points,_=project(cam)
                plane=table_points[(abs(table_points[:,2]-.8)<.006)&(abs(table_points[:,0])<.4)&(abs(table_points[:,1])<.4)]
                if len(plane)>=100:
                    lo,hi=np.quantile(plane,[.02,.98],axis=0)
                    objects['table']=dict(status='available',surface_q02_xyz=lo.tolist(),surface_q98_xyz=hi.tolist(),
                        surface_span_xyz=(hi-lo).tolist(),surface_median_xyz=np.median(plane,axis=0).tolist(),
                        captured_at=cam['captured_at'],source='rgbd_at_calibrated_table_plane',camera='overview')
                self.latest=dict(objects={k:{n:v for n,v in o.items() if n!='mask'} for k,o in objects.items()},
                                 object_versions=self.object_versions.copy(),geometry_captured_at=packet['captured_at'],
                                 geometry_robot_state={k:plain(packet[k]) for k in ('tcp_xyz','tcp_wxyz','gripper_joint_positions','last_gripper_command','captured_at','control_step')})
                self.segmented_packet=(packet,objects);self.perception_error=None
            except Exception as exc:self.perception_error=str(exc)

    def poll_holding_watchdog(self,snapshot):
        watch=self.holding_watch
        if watch is None:return dict(status='inactive')
        obj=watch['object'];result=self.verify({'all':[dict(predicate='holding',object=obj)]},snapshot)
        if snapshot.get('last_gripper_command')=='open':self.holding_watch=None;return dict(status='inactive')
        watch['failures']=watch['failures']+1 if result['status']=='failed' else 0
        if watch['failures']<3:return dict(status='supported' if result['status']=='verified' else 'unknown')
        return dict(status='violated',object_name=obj,evidence=result,reason='public_hold_contradicted',control_step=self.tick)

    def capability_health(self):
        return dict(motion_planner=dict(status='validated' if self.planner_verified else 'unverified',provider='ASPIRE/cuRobo',
            collision_aware=self.planner_verified,collision_scope='observed_scene_and_robot; attached_object_not_modeled'),perception=dict(status='unknown' if self.perception_error else 'configured',error=self.perception_error,mode='multiview' if self.multiview else 'single_view'))

    def verify(self,goal,snapshot):
        if len(goal.get('all',[]))==1 and goal['all'][0]['predicate']=='restored_pose':
            row=self.restoration_done;obj=goal['all'][0]['object']
            ok=bool(row and row['object']==obj and row['revision']==self.episode.revision and
                snapshot.get('last_gripper_command')=='closed' and np.max(abs(self.encoder-np.array(row['joints'])))<.05)
            return dict(status='verified' if ok else 'unknown',reason='recorded_restore_pose_checked' if ok else 'no_current_completed_restore')
        if len(goal.get('all',[]))==1 and goal['all'][0]['predicate']=='recorded_descent':
            obj=goal['all'][0]['object'];record=self.recorded_descent
            ok=bool(record and record['object']==obj and record['revision']==self.episode.revision
                and snapshot.get('last_gripper_command')=='closed'
                and max(abs(self.encoder-np.array(record['trajectory'].joint_positions[-1])))<.05
                and all(snapshot.get('object_versions',{}).get(k)==v for k,v in record['other_versions'].items()))
            return dict(status='verified' if ok else 'unknown',reason='recorded_reverse_path' if ok else 'recorded_restore_not_available',scope='same_goal_and_observed_scene; not_new_motion_certification')
        result=verify(goal,snapshot,list(self.history),self.reference,self.holding_watch)
        # Custody follows authoritative positive sensor evidence, including
        # bound CaP postconditions; it must not depend on calling one particular
        # helper function or on successful return of the whole program.
        if result['status']=='verified':
            held=next((c['object'] for c in goal['all'] if c['predicate']=='holding'),None)
            if held and (self.holding_watch is None or self.holding_watch['object']!=held):
                self.holding_watch=dict(object=held,failures=0,established_evidence_ref=snapshot.get('evidence_ref'))
        return result

    def verification_readiness(self,goals,snapshot):
        rows=[s for s in self.history if s['control_step']>=self.last_action_tick]
        ready=len(rows)>=3 and rows[-1]['captured_at']-rows[0]['captured_at']>=.6
        return dict(status='ready' if ready else 'pending',reason='public_post_action_window',samples=len(rows))

    def _typed_state(self,snapshot):
        from cap_harness.contracts import Pose,RobotState,RobotPlanningContext
        q=np.array(snapshot['joint_positions']);base=self.base_T[:3,3]
        pose=Pose(np.array(snapshot['tcp_xyz'])-base,np.array(snapshot['tcp_wxyz']),'robot_base')
        names=tuple(f'panda_joint{i}' for i in range(1,8))
        state=RobotState(q,np.zeros(7),pose,1. if snapshot['last_gripper_command']=='open' else 0.,'robot_base',joint_names=names)
        context=RobotPlanningContext('robosuite','panda',{'primary':names},{'primary':np.eye(4)},{'primary':'panda_hand'})
        return state,context

    def _geometry_packet(self,obj):
        data=getattr(self,'segmented_packet',None)
        if data is None:raise ValueError('No public segmentation yet')
        packet,objects=data;o=objects.get(obj,{})
        if o.get('status')!='available' or time.time()-o.get('captured_at',0)>1:
            raise ValueError('Current object geometry is unknown or stale')
        return packet,objects,o

    def _plan_pose(self,segment,xyz,quat,exclude=None):
        from cap_harness.contracts import Pose,PlanningScene,PointCloud
        from cap_harness.providers.curobo.client import CuRoboProvider
        self.episode.checkpoint(segment)
        packet=self.raw;cam=packet['cameras']['overview'];points,_=project(cam)
        points=points[(points[:,2]>.75)&(points[:,2]<1.8)]
        # Object-specific carving removes only its measured segmented pixels.
        if exclude is not None:
            p,objects,o=self._geometry_packet(exclude)
            mask=objects[exclude]['mask'];points,_=project(p['cameras'][o['camera']],~mask)
            points=points[(points[:,2]>.75)&(points[:,2]<1.8)]
        points=points[::max(1,len(points)//15000)]-self.base_T[:3,3]
        state,context=self._typed_state(self.episode.observe())
        provider=CuRoboProvider(timeout_s=60,max_retries=0,control_period_s=self.period)
        pose=Pose(np.array(xyz)-self.base_T[:3,3],np.array(quat),'robot_base')
        self.episode.event('motion_plan_requested', target_xyz=list(map(float,xyz)), target_wxyz=list(map(float,quat)),
            robot_base_world=self.base_T[:3,3].tolist(), scene_points=len(points), start_joints=state.joint_positions['primary'].tolist(),
            evidence_ref=self.episode.snapshot.get('evidence_ref'))
        result=provider.plan_to_pose(state,pose,scene=PlanningScene(PointCloud(points,'robot_base')),context=context)
        self.episode.checkpoint(segment)
        if not result.ok or result.trajectory is None:raise ValueError(str(result.error))
        trajectory=result.trajectory
        if not trajectory.collision_aware:raise ValueError('Planner returned an unchecked trajectory')
        self.planner_verified=True
        return trajectory

    def _move_trajectory(self,segment,trajectory):
        e=self.episode
        with e.cv:
            e.checkpoint(segment)
            expected=np.array(trajectory.expected_start.joint_positions['primary'])
            if np.max(np.abs(self.encoder-expected))>.05:raise ValueError('Trajectory start no longer matches encoders')
        for waypoint in trajectory.joint_positions:
            with e.cv:
                e.checkpoint(segment);e.target=np.asarray(waypoint).tolist();e.motion=True;tick=self.tick
                while self.tick<=tick:e.checkpoint(segment);e.cv.wait(.02)
        end=self.tick+40
        with e.cv:
            while np.max(np.abs(self.encoder-np.array(trajectory.joint_positions[-1])))>.03:
                e.checkpoint(segment)
                if self.tick>=end:raise ValueError('Trajectory tracking timeout')
                e.cv.wait(.03)
            e.motion=False;self.last_action_tick=self.tick
        return dict(status='verified',scope='trajectory_tracking_only')

    def tools(self,segment):
        from .tabletop_actions import tools
        return tools(self,segment)

    def _private_score(self):
        # This path writes only the independent evaluator artifact. No return
        # value or state is consumed by sensing, planning or public verification.
        rows={}
        for ident,bid in self.env.object_body_ids.items():
            rows[ident]=dict(position=self.env.sim.data.body_xpos[bid].tolist(),quaternion=self.env.sim.data.body_xquat[bid].tolist())
        with (self.episode.directory/'evaluation-private.jsonl').open('a') as f:
            robot=self.env.robots[0];site=robot.eef_site_id['right']
            extents={k:(np.r_[v[0],v[0],v[1]] if len(v)==2 else v).tolist() for k,v in self.env.dimensions.items()}
            f.write(json.dumps(dict(control_step=self.tick,simulation_time=self.sim_time(),objects=rows,
                half_extents=extents,tcp_xyz=self.env.sim.data.site_xpos[site].tolist(),
                gripper_command=self.episode.gripper))+'\n')

    def close(self):
        self.closing=True
        if self.env is not None:self.env.close()
