"""Fixed public-sensor-only feasibility experiment, not an LLM benchmark.

Actions use Episode.execute and its restricted APIs. The driver reads only
public observation NPZs; private outcome scoring belongs to a separate process.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import time
import numpy as np
from scipy.spatial.transform import Rotation
from agentic_cap.core import Manager, DEFAULTS
from agentic_cap.cap_backend import CaPBackend
from agentic_cap.server import runtime_provenance
from agentic_cap.public_sensors import visible_surface
from experiments.public_cube_geometry import surface_points, fit_faces, public_size_reference, estimate_center


PROTOCOL = dict(inspection_yaw_degrees=[25., -25.], settle_seconds=1.25,
                lift_m=.12, retreat_m=.08, approach_m=.10,
                aside_x_m=-.15, fallback='original_visible_box; not a precision guarantee',
                controller='fixed Python stages, not LLM', limits=DEFAULTS)


def wait_for(fn, seconds=100):
    end = time.monotonic()+seconds
    while time.monotonic() < end:
        value = fn()
        if value:
            return value
        time.sleep(.05)
    raise TimeoutError('Runtime condition did not complete')


def inspection_quaternion(initial_wxyz, degrees):
    initial = Rotation.from_quat(np.asarray(initial_wxyz)[[1, 2, 3, 0]])
    xyzw = (Rotation.from_euler('z', degrees, degrees=True)*initial).as_quat()
    return xyzw[[3, 0, 1, 2]].tolist()


class PublicTrial:
    def __init__(self, episode, mode):
        self.e, self.mode = episode, mode
        self.references = {}
        self.measurements, self.segments = [], []
        self.inspections = 0

    def measure(self, label):
        state = self.e.observe()
        ref = state['evidence_ref']
        if not ref.startswith('frame-') or Path(ref).name != ref or not ref.endswith('.npz'):
            raise ValueError('Invalid public sensor reference')
        with np.load(self.e.directory/ref, allow_pickle=False) as saved:
            packet = {key: saved[key] for key in saved.files}
        if abs(float(packet['captured_at'])-state['captured_at']) > 1e-6:
            raise ValueError('Sensor/state timestamp mismatch')
        result = dict(label=label, evidence_ref=ref, captured_at=state['captured_at'],
                      tcp_xyz=state['tcp_xyz'], tcp_wxyz=state['tcp_wxyz'],
                      control_step=state['control_step'], gripper_command=float(packet['gripper_command']),
                      objects={})
        for name in ['red cube', 'green cube']:
            points = surface_points(packet, name)
            faces = fit_faces(points, packet['camera_pose_xyz_wxyz'][:3])
            prior = self.references.get(name)
            valid_prior = prior if prior and prior['captured_at'] < state['captured_at'] else None
            center = estimate_center(faces, valid_prior)
            obj = visible_surface(packet, name)
            box = ((np.asarray(obj['surface_q05_xyz'])+obj['surface_q95_xyz'])/2).tolist() if obj['status'] == 'visible' else None
            result['objects'][name] = dict(candidate=center, faces=faces, visible=obj,
                                           visible_box_center_xyz=box, size_reference=valid_prior)
            if prior is None and float(packet['gripper_command']) >= .5:
                size = public_size_reference(faces)
                if size is not None:
                    self.references[name] = dict(size, evidence_ref=ref, captured_at=state['captured_at'])
        self.measurements.append(result)
        self.e.event('public_geometry_probe', mode=self.mode, measurement=result)
        return result

    def segment(self, label, code, contract=None):
        key = f'{len(self.segments):02d}-{label}'
        self.e.decision(key, self.e.revision)
        sid = self.e.execute(key, self.e.revision, label, code, contract)
        wait_for(lambda: self.e.active is None, 90)
        result = self.e.segments[sid].copy()
        self.segments.append(result)
        print(json.dumps(dict(episode_id=self.e.id, mode=self.mode, phase=label,
                              execution=result['execution'], error=result.get('error'))), flush=True)
        if result['execution'] != 'completed':
            raise RuntimeError(f'{label}: {result.get("error", result["execution"])}')
        time.sleep(PROTOCOL['settle_seconds'])
        return self.measure(label)

    @staticmethod
    def center(measurement, name):
        obj = measurement['objects'][name]
        candidate = obj['candidate']
        if candidate['status'] == 'estimated':
            return np.asarray(candidate['center_xyz']), 'three_face'
        if obj['visible_box_center_xyz'] is None:
            raise ValueError(f'No public geometry for {name}')
        return np.asarray(obj['visible_box_center_xyz']), 'visible_box_fallback'

    def pick(self, name):
        # Shared existing API localization/top-down grasp for both groups.
        self.segment('grasp-'+name, f'p, unused, extent = get_object_pose({name!r})\n'
                     'open_gripper()\ngoto_pose(p, [0,0,1,0], z_approach=0.10)\nclose_gripper()')
        return self.segment('lift-'+name, 's=get_execution_state()\np=s["tcp_xyz"]\n'
                            f'p[2]=p[2]+{PROTOCOL["lift_m"]}\n'
                            f'goto_pose(p,s["tcp_wxyz"])\ncheck_holding({name!r})',
                            dict(postcondition={'holding': name}))

    def inspect_if_needed(self, measurement, name):
        if self.mode != 'active':
            return measurement
        xyz, original_q = measurement['tcp_xyz'], measurement['tcp_wxyz']
        for angle in PROTOCOL['inspection_yaw_degrees']:
            if measurement['objects'][name]['candidate']['status'] == 'estimated':
                break
            q = inspection_quaternion(original_q, angle)
            self.inspections += 1
            measurement = self.segment(f'inspect-{name}-{angle}',
                f'goto_pose({xyz!r}, {q!r})\ncheck_holding({name!r})',
                dict(preconditions=[{'holding': name}], postcondition={'holding': name}))
        return measurement

    def prepare_placement(self, measurement, name, support):
        return self.inspect_if_needed(measurement, name)

    def placement_evidence(self, measurement, name, support):
        return dict(held_center=measurement['objects'][name]['candidate'],
                    support_visible=measurement['objects'][support]['visible']['status'])

    def place(self, measurement, name, support, aside=False):
        obj, held_method = self.center(measurement, name)
        target, support_method = self.center(measurement, support)
        offset = obj-np.asarray(measurement['tcp_xyz'])
        if aside:
            target[0] += PROTOCOL['aside_x_m']
        else:
            prior = measurement['objects'][support]['size_reference']
            size = prior['side_m'] if prior else max(measurement['objects'][support]['visible']['surface_span_xyz'][:2])
            target[2] += size
        tcp = (target-offset).tolist()
        retreat = [tcp[0], tcp[1], tcp[2]+PROTOCOL['retreat_m']]
        q = measurement['tcp_wxyz']
        self.e.event('placement_geometry_selected', object_name=name, support=support,
                     held_method=held_method, support_method=support_method,
                     measured_offset_xyz=offset.tolist(), source_ref=measurement['evidence_ref'])
        return self.segment('release-'+name,
            f'goto_pose({tcp!r}, {q!r}, z_approach=0.10)\nopen_gripper()\ngoto_pose({retreat!r}, {q!r})',
            dict(preconditions=[{'holding': name}],
                 **({} if aside else {'postcondition': {'on': [name, support], 'empty_gripper': True}})))


def run_plan(args, trial_type=PublicTrial, protocol=PROTOCOL):
    root = args.records.resolve()
    root.mkdir(parents=True, exist_ok=False)
    plan = json.loads(args.plan.read_text())
    files = [*Path(__file__).parent.glob('*.py'), *Path(__file__).parent.parent.joinpath('agentic_cap').glob('*.py')]
    sources = {str(file.resolve()): hashlib.sha256(file.read_bytes()).hexdigest() for file in sorted(files)}
    manifest = dict(protocol=protocol, cases=plan, source_sha256=sources,
                    runtime=runtime_provenance('cap', args.upstream),
                    scope='fixed-controller feasibility; no LLM calls; all failures retained')
    (root/'experiment.json').write_text(json.dumps(manifest, indent=2))
    manager = Manager(root, lambda e: CaPBackend(e, args.upstream, evaluation=True))
    results = []
    for index, case in enumerate(plan):
        if sources != {str(file.resolve()): hashlib.sha256(file.read_bytes()).hexdigest() for file in sorted(files)}:
            raise RuntimeError('Experiment source changed during run')
        goal = {'on': ['red cube', 'green cube'], 'empty_gripper': True}
        state = manager.create(f'case-{index}', case['task'], case['seed'], goal)
        e = manager.episodes[state['id']]
        trial = trial_type(e, case['mode'])
        result = dict(case=case, episode_id=e.id, index=index, status='running')
        try:
            wait_for(lambda: e.snapshot or e.status == 'failed', 150)
            if e.status == 'failed':
                raise RuntimeError(str(e.events[-1]))
            e.decision('initial-observation', e.revision)
            trial.measure('initial-observation')
            if case['task'] == 'cube_restack':
                measurement = trial.pick('green cube')
                trial.place(measurement, 'green cube', 'red cube', aside=True)
            measurement = trial.pick('red cube')
            result['before_inspection'] = measurement['objects']['red cube']['candidate']
            result['before_placement_evidence'] = trial.placement_evidence(measurement, 'red cube', 'green cube')
            measurement = trial.prepare_placement(measurement, 'red cube', 'green cube')
            result['after_inspection'] = measurement['objects']['red cube']['candidate']
            result['after_placement_evidence'] = trial.placement_evidence(measurement, 'red cube', 'green cube')
            trial.place(measurement, 'red cube', 'green cube')
            e.decision('finish', e.revision)
            result['verification'] = e.finish(e.revision)
            result['status'] = 'completed' if result['verification']['status'] == 'verified' else 'needs_attention'
        except Exception as exc:
            result.update(status='needs_attention', error=str(exc))
        finally:
            # Independent end window; no actions. Included separately in cost.
            time.sleep(PROTOCOL['settle_seconds'])
            result.update(inspections=trial.inspections, measurements=trial.measurements,
                          segments=trial.segments, used=e.state()['used'])
            e.close()
            result['episode_state'] = e.state()
        results.append(result)
        (root/'results.json').write_text(json.dumps(results, indent=2))
        print(json.dumps(dict(case=case, episode_id=e.id, status=result['status'],
                              inspections=result['inspections'], error=result.get('error'))), flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--upstream', required=True)
    p.add_argument('--records', type=Path, required=True)
    p.add_argument('--plan', type=Path, required=True)
    run_plan(p.parse_args())


if __name__ == '__main__':
    main()
