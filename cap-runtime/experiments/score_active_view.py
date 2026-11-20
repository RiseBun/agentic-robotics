"""Independent scorer; never imported by the fixed public-sensor controller."""
from __future__ import annotations
import argparse
from bisect import bisect_left
from collections import Counter
import hashlib
import json
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation


def stable_outcome(samples, start, end):
    window = [r for r in samples if start <= r['time'] <= end]
    if len(window) < 2 or window[-1]['time']-window[0]['time'] < .5:
        return dict(status='unknown', reason='insufficient_settle_samples', sample_count=len(window))
    values = [r['task_completed'] for r in window]
    status = 'failed' if any(v is False for v in values) else 'verified' if all(v is True for v in values) else 'unknown'
    return dict(status=status, sample_count=len(window), span_seconds=window[-1]['time']-window[0]['time'],
                values=values, scope='observed_end_window_only')


def center_reference(samples, time, name):
    index = bisect_left([r['time'] for r in samples], time)
    if index == 0 or index == len(samples):
        return dict(status='unknown', reason='no_bracketing_truth')
    a, b = samples[index-1:index+1]
    if name not in a['world_positions'] or name not in b['world_positions']:
        return dict(status='unknown', reason='no_private_object')
    if 'world_from_cap_robot_base_xyz_wxyz' not in a or 'world_from_cap_robot_base_xyz_wxyz' not in b:
        return dict(status='unknown', reason='no_actual_frame_transform')
    ta = np.asarray(a['world_from_cap_robot_base_xyz_wxyz'])
    tb = np.asarray(b['world_from_cap_robot_base_xyz_wxyz'])
    ra = Rotation.from_quat(ta[3:][[1, 2, 3, 0]])
    rb = Rotation.from_quat(tb[3:][[1, 2, 3, 0]])
    pa, pb = np.asarray(a['world_positions'][name]), np.asarray(b['world_positions'][name])
    if (b['time']-a['time'] > .65 or np.linalg.norm(pb-pa) > .005 or
            np.linalg.norm(ta[:3]-tb[:3]) > 1e-6 or (ra.inv()*rb).magnitude() > 1e-6):
        return dict(status='unknown', reason='truth_or_base_not_stable')
    position = pa+(pb-pa)*(time-a['time'])/(b['time']-a['time'])
    return dict(status='scored', center_base_xyz=ra.inv().apply(position-ta[:3]).tolist(),
                bracket_seconds=b['time']-a['time'], scope='interpolated_stable_private_window')


def score_trial(root, trial, settle):
    episode = root/trial['episode_id']
    samples = [json.loads(line) for line in (episode/'evaluation-private.jsonl').read_text().splitlines()]
    events = [json.loads(line) for line in (episode/'events.jsonl').read_text().splitlines()]
    closed = next(r['time'] for r in reversed(events) if r['kind'] == 'episode_closed')
    outcome = stable_outcome(samples, closed-settle, closed)
    declared = trial['status'] == 'completed'
    geometry = []
    for measurement in trial['measurements']:
        for name, obj in measurement['objects'].items():
            reference = center_reference(samples, measurement['captured_at'], name)
            row = dict(label=measurement['label'], evidence_ref=measurement['evidence_ref'],
                       object_name=name, candidate_status=obj['candidate']['status'], reference=reference)
            if reference['status'] == 'scored':
                center = np.asarray(reference['center_base_xyz'])
                if obj['visible_box_center_xyz'] is not None:
                    row['old_xy_error_m'] = float(np.linalg.norm((obj['visible_box_center_xyz']-center)[:2]))
                if obj['candidate']['status'] == 'estimated':
                    row['candidate_xy_error_m'] = float(np.linalg.norm((obj['candidate']['center_xyz']-center)[:2]))
            geometry.append(row)
    inspection_contact = []
    for segment in trial['segments']:
        if not segment['subgoal'].startswith('inspect-'):
            continue
        matching = [r for r in events if r.get('segment_id') == segment['id'] or r.get('segment', {}).get('id') == segment['id']]
        if matching:
            start, end = matching[0]['time'], matching[-1]['time']
            held = [r['bilateral_contact'].get('red cube') for r in samples if start <= r['time'] <= end]
            inspection_contact.append(dict(segment_id=segment['id'], sample_count=len(held),
                                           bilateral_contact_loss_observed=any(v is False for v in held),
                                           scope='contact proxy; not a calibrated slip/drop detector'))
    return dict(case=trial['case'], episode_id=trial['episode_id'], status=trial['status'],
                error=trial.get('error'), declared_complete=declared, private_outcome=outcome,
                false_completion=declared and outcome['status'] == 'failed',
                reset_count=sum(r['kind'] == 'backend_configured' for r in events),
                before_inspection=trial.get('before_inspection'), after_inspection=trial.get('after_inspection'),
                before_placement_evidence=trial.get('before_placement_evidence') or {},
                after_placement_evidence=trial.get('after_placement_evidence') or {},
                inspections=trial['inspections'], used=trial['used'],
                inspection_contact=inspection_contact, geometry=geometry)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--records', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    results = json.loads((args.records/'results.json').read_text())
    plan = json.loads((args.records/'experiment.json').read_text())
    scored = [score_trial(args.records, trial, plan['protocol']['settle_seconds']) for trial in results]
    groups = {}
    for mode in sorted(set(row['case']['mode'] for row in scored)):
        rows = [r for r in scored if r['case']['mode'] == mode]
        groups[mode] = dict(trials=len(rows), outcomes=dict(Counter(r['private_outcome']['status'] for r in rows)),
                            false_completions=sum(r['false_completion'] for r in rows),
                            inspections=sum(r['inspections'] for r in rows),
                            support_unknown_to_available_trials=sum(
                                r['before_placement_evidence'].get('support', {}).get('status') == 'unknown'
                                and r['after_placement_evidence'].get('support', {}).get('status') == 'available'
                                for r in rows),
                            center_unknown_to_estimated_trials=sum(r.get('before_inspection', {}).get('status') == 'unknown'
                                and r.get('after_inspection', {}).get('status') == 'estimated' for r in rows))
    output = dict(planned_trials=len(plan['cases']), observed_trials=len(scored), trials=scored, groups=groups,
                  scorer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  limitations=['Fixed controller, no LLM cost or policy-performance claim',
                               'One trial per task/seed/mode; seen and unseen initial states separate',
                               'End window does not guarantee future stability',
                               'Controller never reads this private evaluation file'])
    args.output.write_text(json.dumps(output, indent=2))
    print(json.dumps(dict(planned=output['planned_trials'], observed=output['observed_trials'], groups=groups), indent=2))


if __name__ == '__main__':
    main()
