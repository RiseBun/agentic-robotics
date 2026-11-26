"""Independent CLOSED-task scoring. Never imported by online policy modules."""
import json
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation
from .tabletop_scene import REGIONS, public_catalog


def score_episode(directory,expected_goal=None):
    root=Path(directory);episode=json.loads((root/'episode.json').read_text())
    if episode['status']!='closed' or episode['active_segment'] is not None:
        raise ValueError('Independent scoring requires a closed task')
    file=root/'evaluation-private.jsonl'
    if not file.exists():return dict(episode_id=episode['id'],status='unscored',reason='private_physics_record_missing')
    rows=[json.loads(line) for line in file.read_text().splitlines() if line]
    if not rows or 'half_extents' not in rows[-1]:return dict(episode_id=episode['id'],status='unscored',reason='physics_record_incomplete')
    recent=[r for r in rows if r['simulation_time']>=rows[-1]['simulation_time']-1.2]
    if len(recent)<3 or recent[-1]['simulation_time']-recent[0]['simulation_time']<.5:
        return dict(episode_id=episode['id'],status='unscored',reason='final_physical_window_incomplete')
    def condition(c,row):
        pred=c['predicate'];obj=c.get('object');objects=row['objects'];sizes=row['half_extents']
        if pred=='empty_gripper':return row['gripper_command']>.5
        pos=np.array(objects[obj]['position']);half=np.array(sizes[obj])
        q=np.array(objects[obj]['quaternion']);rotation=Rotation.from_quat(q[[1,2,3,0]]).as_matrix()
        if public_catalog()['objects'].get(obj,{}).get('shape')=='cylinder':
            axis=rotation[:,2];extent=half[0]*np.sqrt(np.maximum(0,1-axis**2))+half[2]*abs(axis)
        else:extent=abs(rotation)@half
        bottom=pos[2]-extent[2]
        if pred=='holding':return row['gripper_command']<.5 and pos[2]>.87 and np.linalg.norm(pos-np.array(row['tcp_xyz']))<.09
        if pred=='on':
            support=c['support']
            if support=='table':return abs(bottom-.8)<.012
            s=np.array(objects[support]['position']);sq=np.array(objects[support]['quaternion'])
            se=abs(Rotation.from_quat(sq[[1,2,3,0]]).as_matrix())@np.array(sizes[support])
            return bool(np.all(abs(pos[:2]-s[:2])<se[:2]) and abs(bottom-(s[2]+se[2]))<.012)
        if pred=='in_region':
            r=REGIONS[c['region']];lo,hi=np.array(r['bounds_xy'])
            return bool(np.all(pos[:2]-extent[:2]>=lo) and np.all(pos[:2]+extent[:2]<=hi) and abs(bottom-.8)<.012)
        return False
    requested=expected_goal or episode['goal']
    all_rows=all(all(condition(c,row) for c in requested['all']) for row in recent)
    canonical=lambda g:sorted(json.dumps(c,sort_keys=True) for c in g['all'])
    intent_match=canonical(requested)==canonical(episode['goal'])
    events=[json.loads(line) for line in (root/'events.jsonl').read_text().splitlines() if line]
    public=any(e['kind']=='finish_checked' and e.get('result',{}).get('status')=='verified' for e in events)
    return dict(episode_id=episode['id'],seed=episode['seed'],status='scored',physical_verified=all_rows,
        public_verified=public,false_completion=public and not all_rows,used=episode['used'],
        evaluator_version=3,requested_goal=requested,registered_goal=episode['goal'],registered_goal_matches_expected=intent_match,
        physical_window_seconds=recent[-1]['simulation_time']-recent[0]['simulation_time'],
        scope='limited rigid tabletop evaluator; no universal physical correctness claim')


def score_closed_batch(root,expected_goal=None):
    paths=sorted(Path(root).glob('*/episode.json'))
    if any(json.loads(p.read_text())['status']!='closed' for p in paths):raise ValueError('Close every episode before batch scoring')
    return [score_episode(p.parent,expected_goal) for p in paths]
