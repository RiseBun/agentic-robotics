"""Private, post-execution scorer for frozen public geometry predictions.

Legacy archives lack measured frame transforms. Require the caller to supply
and label a source-derived transform; never silently compare world/base axes.
Only stable bracketing truth windows are primary scoring evidence.
"""
from __future__ import annotations
import argparse
from bisect import bisect_left
from collections import Counter
import hashlib
import json
from pathlib import Path
import zipfile
import numpy as np
from scipy.spatial.transform import Rotation


def errors(rows, key):
    values = [row[key] for row in rows if row.get(key) is not None]
    if not values:
        return dict(count=0)
    return dict(count=len(values), mean_m=float(np.mean(values)),
                median_m=float(np.median(values)), p90_m=float(np.quantile(values, .90)),
                maximum_m=float(np.max(values)))


def score(rows, root, translation, quaternion):
    cache, output = {}, []
    rotation = Rotation.from_quat(np.asarray(quaternion)[[1, 2, 3, 0]])
    for row in rows:
        if not row['requested_by_model']:
            continue
        trial = row['trial']
        if trial not in cache:
            # Read truth only now, after the public prediction file is frozen.
            with zipfile.ZipFile(root/trial/'runtime.zip') as archive:
                names = [n for n in archive.namelist() if n.endswith('/evaluation-private.jsonl')]
                if len(names) != 1:
                    raise ValueError(f'Ambiguous private evaluation log: {trial}')
                truth = [json.loads(line) for line in archive.read(names[0]).decode().splitlines()]
            truth.sort(key=lambda item: item['time'])
            cache[trial] = (truth, [item['time'] for item in truth])
        truth, times = cache[trial]
        time = row['captured_at']
        after = bisect_left(times, time)
        record = dict(trial=trial, evidence_ref=row['evidence_ref'], object_name=row['object_name'],
                      gripper_command=row['gripper_command'], candidate=row['candidate'],
                      public_size_reference=row['public_size_reference'], cpu_elapsed_ms=row['cpu_elapsed_ms'])
        if after == 0 or after == len(times):
            output.append(dict(record, scoring_status='unknown', reason='no_bracketing_truth'))
            continue
        a, b = truth[after-1], truth[after]
        name = row['object_name']
        if name not in a['world_positions'] or name not in b['world_positions']:
            output.append(dict(record, scoring_status='unknown', reason='no_private_object'))
            continue
        duration = b['time']-a['time']
        pa, pb = np.asarray(a['world_positions'][name]), np.asarray(b['world_positions'][name])
        displacement = float(np.linalg.norm(pb-pa))
        record.update(truth_window_seconds=duration, truth_window_displacement_m=displacement)
        if duration > .65 or displacement > .005:
            output.append(dict(record, scoring_status='unknown', reason='truth_window_not_stable'))
            continue
        position = pa + (pb-pa)*((time-a['time'])/duration)
        base_position = rotation.inv().apply(position-np.asarray(translation))
        ca, cb = a['bilateral_contact'].get(name), b['bilateral_contact'].get(name)
        record.update(scoring_status='scored', private_bilateral_contact=ca if ca == cb else None,
                      private_reference_center_base_xyz=base_position.tolist())
        for key, point in [('old', row['old_visible_box_center_xyz']),
                           ('candidate', row['candidate'].get('center_xyz'))]:
            if point is not None:
                delta = np.asarray(point)-base_position
                record[key+'_xy_error_m'] = float(np.linalg.norm(delta[:2]))
                record[key+'_xyz_error_m'] = float(np.linalg.norm(delta))
        output.append(record)
    return output


def summarize(rows):
    scored = [r for r in rows if r['scoring_status'] == 'scored']
    common = [r for r in scored if r.get('candidate_xy_error_m') is not None
              and r.get('old_xy_error_m') is not None]
    held = [r for r in scored if r.get('private_bilateral_contact') is True]
    return dict(requested_object_frames=len(rows), stable_scored_frames=len(scored),
                scoring_rejections=dict(Counter(r['reason'] for r in rows if r['scoring_status'] != 'scored')),
                candidate_status_counts=dict(Counter(r['candidate']['reason'] for r in rows)),
                candidate_estimated_frames=sum(r['candidate']['status'] == 'estimated' for r in rows),
                earlier_public_size_available=sum(r['public_size_reference'] is not None for r in rows),
                old_all_stable_xy=errors(scored, 'old_xy_error_m'),
                old_stable_held_xy=errors(held, 'old_xy_error_m'),
                paired_accepted_xy=dict(old=errors(common, 'old_xy_error_m'),
                                         candidate=errors(common, 'candidate_xy_error_m')),
                cpu_ms=dict(median=float(np.median([r['cpu_elapsed_ms'] for r in rows])),
                            p90=float(np.quantile([r['cpu_elapsed_ms'] for r in rows], .9))))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--experiment', type=Path, required=True)
    p.add_argument('--records-root', type=Path, required=True)
    p.add_argument('--legacy-base-world-xyz', type=float, nargs=3, required=True)
    p.add_argument('--legacy-base-world-wxyz', type=float, nargs=4, required=True)
    args = p.parse_args()
    predictions = args.experiment/'public-predictions.jsonl'
    manifest = json.loads((args.experiment/'prediction-manifest.json').read_text())
    digest = hashlib.sha256(predictions.read_bytes()).hexdigest()
    if digest != manifest['predictions_sha256']:
        raise ValueError('Public predictions changed after freezing')
    for trial in manifest['trials']:
        archive = args.records_root/trial['trial']/'runtime.zip'
        if hashlib.sha256(archive.read_bytes()).hexdigest() != trial['archive_sha256']:
            raise ValueError(f'Archive changed: {archive}')
    rows = [json.loads(line) for line in predictions.read_text().splitlines()]
    scored = score(rows, args.records_root, args.legacy_base_world_xyz, args.legacy_base_world_wxyz)
    (args.experiment/'private-geometry-scores.jsonl').write_text(
        ''.join(json.dumps(row)+'\n' for row in scored), encoding='utf-8')
    summary = dict(summary=summarize(scored),
                   groups={label: summarize([r for r in scored if r['trial'].startswith(label)])
                           for label in ['baseline-seeds11-12', 'pilot-seed11']},
                   prediction_sha256=digest, scorer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                   frame_transform=dict(source='legacy deployment Panda/RethinkMount XML, not per-frame measured calibration',
                                        world_from_base_xyz=args.legacy_base_world_xyz,
                                        world_from_base_wxyz=args.legacy_base_world_wxyz),
                   truth_matching=dict(maximum_bracket_seconds=.65, maximum_object_displacement_m=.005,
                                       interpolation='linear within stable bracket; no exact synchronized truth'),
                   limitations=['Seen development data only; no heldout or closed-loop evidence',
                                'Depth quantized 1mm; both estimators replay the same archived sensor values',
                                'Primary model-input observations only; not every API holding-window sample',
                                'Rejected frames retained; accepted-only accuracy is selection-conditioned'])
    (args.experiment/'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary['summary'], indent=2))


if __name__ == '__main__':
    main()
