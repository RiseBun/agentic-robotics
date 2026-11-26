"""Public image provenance and measurement identity; no simulator access."""
import math
from pathlib import Path
import numpy as np

ROBOT_FIELDS=('joint_positions','tcp_xyz','tcp_wxyz','gripper_joint_positions','last_gripper_command','captured_at','control_step')

def stamp_packet(packet,episode_id):
    packet['episode_id']=episode_id
    for camera,cam in packet['cameras'].items():
        stem=(f"frame-{packet['control_step']:06d}" if camera=='overview' else f"view-robot0_eye_in_hand-{packet['control_step']:06d}")
        cam.update(evidence_ref=stem+'.npz',image_ref=stem+'.png',control_step=packet['control_step'])
    return packet

def source_for(packet,cameras):
    return dict(schema=1,episode_id=packet['episode_id'],control_step=packet['control_step'],
        captured_at=packet['captured_at'],frame='world',length_unit='meter',
        observations=[dict(camera=name,control_step=packet['control_step'],
            captured_at=packet['cameras'][name]['captured_at'],
            evidence_ref=packet['cameras'][name]['evidence_ref'],
            image_ref=packet['cameras'][name]['image_ref']) for name in cameras])

def save_camera(directory,packet,name):
    cam=packet['cameras'][name]
    values={key:packet[key] for key in ROBOT_FIELDS}
    values.update(rgb=cam['rgb'],depth=np.asarray(cam['depth'],dtype=np.float32),
        intrinsics=cam['intrinsics'],camera_to_world=cam['camera_to_world'],
        camera_captured_at=cam['captured_at'],camera_name=name,episode_id=packet['episode_id'],schema=1)
    with (Path(directory)/cam['evidence_ref']).open('wb') as stream:
        np.savez_compressed(stream,**values)

def geometry_problem(obj,snapshot):
    """Strict provenance for versioned real packets; legacy synchronous fixtures remain supported."""
    if snapshot.get('geometry_evidence_schema')!=1:return None
    src=obj.get('evidence_source',{})
    try:
        if src.get('schema')!=1 or src.get('episode_id')!=snapshot.get('episode_id'):return 'geometry_source_missing_or_wrong_episode'
        if src.get('frame')!='world' or src.get('length_unit')!='meter':return 'geometry_coordinate_mismatch'
        step=src['control_step'];at=float(src['captured_at']);now=float(snapshot['captured_at'])
        if not isinstance(step,int) or step>snapshot['control_step'] or step<snapshot.get('geometry_not_before_step',0):return 'geometry_pre_action_or_future_step'
        if not math.isfinite(at) or not 0<=now-at<=1.:return 'geometry_source_stale_or_future'
        measured=snapshot['geometry_robot_state']
        if measured['control_step']!=step or abs(measured['captured_at']-at)>1e-6:return 'geometry_robot_pairing_mismatch'
        observations=src['observations']
        if not observations:return 'geometry_camera_source_missing'
        names=[]
        for cam in observations:
            if cam['control_step']!=step or not cam['evidence_ref'] or not cam['image_ref']:return 'geometry_camera_source_mismatch'
            if not math.isfinite(cam['captured_at']) or not 0<=at-cam['captured_at']<=.05:return 'geometry_camera_time_mismatch'
            names.append(cam['camera'])
        expected=obj.get('contributing_cameras',[obj.get('camera')])
        if len(names)!=len(set(names)) or set(names)!=set(expected):return 'geometry_camera_identity_mismatch'
        if abs(float(obj['captured_at'])-at)>.05:return 'geometry_measurement_time_mismatch'
    except (KeyError,TypeError,ValueError,OverflowError):return 'geometry_source_malformed'
    return None

def measurement(obj,snapshot):
    source=obj.get('evidence_source') or snapshot.get('geometry_robot_state') or snapshot
    return (source.get('control_step',snapshot['control_step']),source.get('captured_at',obj.get('captured_at')))

def placement_window(history,objects,not_before_step=0):
    """Repeated publication cannot manufacture independent physical measurements."""
    seen=set();per_object={name:set() for name in objects};rows=[];times=[]
    for row in history:
        ids=[];at=[]
        for ident in objects:
            obj=row.get('objects',{}).get(ident,{})
            if obj.get('status')!='available' or geometry_problem(obj,row):return [],[]
            step,t=measurement(obj,row)
            if step<not_before_step:continue
            if t is None or not math.isfinite(t) or not 0<=row['captured_at']-t<=1.:return [],[]
            ids.append((step,t));at.append(t)
        if len(ids)!=len(objects):continue
        key=tuple(ids)
        if key in seen or any(identity in per_object[name] for name,identity in zip(objects,ids)):continue
        seen.add(key)
        for name,identity in zip(objects,ids):per_object[name].add(identity)
        rows.append(row);times.append(min(at))
    return rows,times
