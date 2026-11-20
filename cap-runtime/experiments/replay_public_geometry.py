"""Replay actual model-input frames without reading private simulation truth.

Usage: PYTHONPATH=cap-runtime python -m experiments.replay_public_geometry
       --records-root <paired-campaign-directory> --output <new-directory>
Runs all archived baseline/candidate trials. No simulator, LLM, reset or action.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile
import time
import zipfile

import numpy as np
import agentic_cap.public_sensors as public_sensors
from agentic_cap.public_sensors import visible_surface
from experiments.public_cube_geometry import (PROTOCOL, surface_points, fit_faces,
                                                public_size_reference, estimate_center)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def contexts(workbench):
    result = {}
    for line in workbench.read_text(encoding='utf-8').splitlines():
        row = json.loads(line)
        if row.get('kind') != 'model_request':
            continue
        if row.get('protocol') != 'responses':
            raise ValueError('This replay only handles the archived Responses protocol')
        for item in row['body']['input']:
            for part in item.get('content', []):
                if part.get('type') == 'input_text':
                    context = json.loads(part['text'])
                    if context.get('mode') == 'execution':
                        obs = context['latest_observation']
                        result.setdefault(obs['evidence_ref'], []).append(obs)
    return result


def predict_trial(root, archive):
    trial = archive.parent
    workbenches = list(trial.glob('records/*/workbench.jsonl'))
    if len(workbenches) != 1:
        raise ValueError(f'Expected exactly one workbench: {trial}')
    requested = contexts(workbenches[0])
    packets = {}
    initial_ref = None
    with zipfile.ZipFile(archive) as outer:
        with tarfile.open(fileobj=outer.open('observations.tar.xz'), mode='r|xz') as inner:
            for entry in inner:
                name = Path(entry.name).name
                if name.startswith('frame-') and name.endswith('.npz'):
                    if initial_ref is None:
                        initial_ref = name
                    if name in requested or name == initial_ref:
                        with np.load(io.BytesIO(inner.extractfile(entry).read()), allow_pickle=False) as saved:
                            packets[name] = {k: saved[k] for k in saved.files}
    missing = set(requested)-set(packets)
    if missing:
        raise ValueError(f'Missing model-input frames: {trial} {missing}')
    if initial_ref is None:
        raise ValueError(f'No archived frames: {trial}')
    earliest = min(packets, key=lambda k: int(packets[k]['control_step']))
    if initial_ref != earliest:
        raise ValueError('Archive order is not chronological; initial reference is ambiguous')
    reference = {}
    output = []
    for ref, packet in sorted(packets.items(), key=lambda item: float(item[1]['captured_at'])):
        for name in ['red cube', 'green cube']:
            started = time.perf_counter()
            points = surface_points(packet, name)
            old = visible_surface(packet, name)
            if old['status'] == 'visible':
                # Guard against duplicated preprocessing silently changing the baseline.
                lo, hi = np.quantile(points, [.05, .95], axis=0)
                np.testing.assert_allclose(lo, old['surface_q05_xyz'], atol=1e-12)
                np.testing.assert_allclose(hi, old['surface_q95_xyz'], atol=1e-12)
                old_center = ((lo+hi)/2).tolist()
            else:
                old_center = None
            faces = fit_faces(points, packet['camera_pose_xyz_wxyz'][:3])
            prior = reference.get(name)
            estimate = estimate_center(faces, prior)
            elapsed_ms = (time.perf_counter()-started)*1000
            row = dict(trial=str(trial.relative_to(root)), object_name=name,
                       evidence_ref=ref, requested_by_model=ref in requested,
                       model_request_reuses=len(requested.get(ref, [])),
                       captured_at=float(packet['captured_at']),
                       control_step=int(packet['control_step']),
                       depth_quantization_m=float(packet['depth_quantization_m']),
                       camera_pose_xyz_wxyz=packet['camera_pose_xyz_wxyz'].tolist(),
                       tcp_xyz=requested[ref][0]['tcp_xyz'] if ref in requested else None,
                       gripper_command=float(packet['gripper_command']),
                       visible_point_count=len(points), old_visible_box_center_xyz=old_center,
                       face_evidence=faces, public_size_reference=prior,
                       candidate=estimate, cpu_elapsed_ms=elapsed_ms)
            output.append(row)
            # A size may inform only later frames, never its own prediction.
            if name not in reference and float(packet['gripper_command']) >= .5:
                size = public_size_reference(faces)
                if size is not None:
                    reference[name] = dict(size, evidence_ref=ref,
                                           captured_at=float(packet['captured_at']))
    return output, dict(trial=str(trial.relative_to(root)), archive_sha256=sha(archive),
                        workbench_sha256=sha(workbenches[0]),
                        requested_frames=len(requested), initial_frame=initial_ref)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--records-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = args.records_root.resolve()
    dependencies = [Path(__file__), Path(__file__).with_name('public_cube_geometry.py'),
                    Path(public_sensors.__file__)]
    source_before = {str(path): sha(path) for path in dependencies}
    args.output.mkdir(parents=True, exist_ok=False)
    archives = sorted(root.glob('*/cube_*-s*-r*/runtime.zip'))
    if not archives:
        raise ValueError('No archived robot trials found')
    all_rows, trials = [], []
    for archive in archives:
        rows, manifest = predict_trial(root, archive)
        all_rows.extend(rows)
        trials.append(manifest)
        print(json.dumps(dict(trial=manifest['trial'], rows=len(rows)), ensure_ascii=False), flush=True)
    predictions = args.output/'public-predictions.jsonl'
    predictions.write_text(''.join(json.dumps(row)+'\n' for row in all_rows), encoding='utf-8')
    if source_before != {str(path): sha(path) for path in dependencies}:
        raise RuntimeError('Replay/geometry/sensor source changed during replay; do not score these predictions')
    manifest = dict(protocol=PROTOCOL, estimator_sha256=sha(Path(__file__).with_name('public_cube_geometry.py')),
                    replay_sha256=sha(Path(__file__)), predictions_sha256=sha(predictions),
                    source_dependencies=source_before,
                    scope='seen seeds 11/12; development diagnostic; not task-success evaluation',
                    data_boundary='estimator and reference selection read public RGBD only; no private truth',
                    coordinate_frame='CaP base (fixed_mount0_base), metres, camera quaternion WXYZ',
                    trials=trials)
    (args.output/'prediction-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps(dict(trials=len(trials), rows=len(all_rows), output=str(args.output))))


if __name__ == '__main__':
    main()
