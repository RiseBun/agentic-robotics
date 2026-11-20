"""Real-physics recovery diagnosis; explicitly synthetic public RGB-D faults.

The fixed driver never reads evaluator data. All four cases close before a
separate scorer may read truth. No robot model or code is trained here.
"""
import argparse
import copy
import hashlib
import json
import os
import time
from pathlib import Path

from agentic_cap.active_evidence import support_evidence
from agentic_cap.cap_backend import CaPBackend
from agentic_cap.core import Manager, StaleRevision
from agentic_cap.server import runtime_provenance
from experiments.active_view_trial import wait_for
from experiments.runtime_support_trial import RuntimeSupportTrial

CASES = ['retreat_recovers', 'persistent_unknown', 'cancel_retreat', 'change_goal_retreat']


class DescentFaultBackend(CaPBackend):
    """Diagnostic only: mask green after a recorded public descent."""
    def __init__(self, episode, upstream, mode):
        super().__init__(episode, upstream, evaluation=True)
        self.diagnosis_mode, self.diagnosis_anchor = mode, None
        self.diagnosis_cleared = False

    def apply_camera_fault(self, rgb, depth):
        from agentic_cap.public_sensors import mask_public_support
        anchor = getattr(self, 'safe_retreat', None)
        if self.diagnosis_anchor is None and anchor is not None:
            self.diagnosis_anchor = copy.deepcopy(anchor)
            self.episode.event('diagnostic_descent_fault_armed', mode=self.diagnosis_mode,
                               anchor=self.diagnosis_anchor)
        if self.diagnosis_anchor is not None and not self.diagnosis_cleared:
            xyz, _ = self.monitor.fk(self.encoder)
            restored = float(xyz[2]) >= self.diagnosis_anchor['start_xyz'][2]-.01
            if self.diagnosis_mode != 'persistent_unknown' and restored:
                self.diagnosis_cleared = True
                self.episode.event('diagnostic_descent_fault_cleared', actual_tcp_xyz=xyz.tolist())
            else:
                return mask_public_support(rgb, depth, 'green cube')
        return rgb, depth, False


class RetreatTrial(RuntimeSupportTrial):
    def stage(self, label, code, expected='completed'):
        request = f'{len(self.segments):02d}-{label}'
        self.e.decision(request, self.e.revision)
        sid = self.e.execute(request, self.e.revision, label, code)
        wait_for(lambda: self.e.active is None, 90)
        result = copy.deepcopy(self.e.segments[sid])
        self.segments.append(result)
        print(json.dumps(dict(episode_id=self.e.id, phase=label, execution=result['execution'],
                              error=result.get('error'))), flush=True)
        if result['execution'] != expected:
            raise RuntimeError(f'{label}: expected {expected}, got {result["execution"]}: {result.get("error")}')
        return result

    def reach_low_pose(self):
        measurement = self.pick('red cube')
        measurement = self.prepare_placement(measurement, 'red cube', 'green cube')
        import numpy as np
        obj, _ = self.center(measurement, 'red cube')
        support, _ = self.center(measurement, 'green cube')
        prior = measurement['objects']['green cube']['size_reference']
        size = prior['side_m'] if prior else max(measurement['objects']['green cube']['visible']['surface_span_xyz'][:2])
        support[2] += size+.015  # Public geometry, 15 mm diagnostic hover gap.
        low = (support-(obj-np.asarray(measurement['tcp_xyz']))).tolist()
        high = [low[0], low[1], low[2]+.12]
        q = measurement['tcp_wxyz']
        self.e.event('diagnostic_public_hover_selected', source_ref=measurement['evidence_ref'],
                     low_tcp_xyz=low, high_tcp_xyz=high, hover_gap_m=.015)
        self.stage('reach-hover-origin', f'goto_pose({high!r}, {q!r})')
        # Joint-space arrival has finite Cartesian error. A vertical move must
        # start from the measured reached pose, not the previous requested XY.
        before = self.e.observe()
        low = list(before['tcp_xyz'])
        low[2] -= .12
        q = before['tcp_wxyz']
        self.e.event('diagnostic_measured_descent_selected', source_ref=before['evidence_ref'],
                     start_xyz=before['tcp_xyz'], target_xyz=low, target_wxyz=q)
        self.stage('vertical-descent', f'goto_pose({low!r}, {q!r})')
        wait_for(lambda: support_evidence(self.e.observe(), 'green cube', self.e.backend.reference)['status'] == 'unknown', 4)
        blocked = self.stage('release-must-be-blocked', 'open_gripper()', 'failed')
        recovery = blocked.get('feedback', {}).get('evidence', {}).get('recovery', {})
        if recovery.get('status') != 'available':
            raise RuntimeError('Blocked release did not expose an available recorded retreat')
        low_inspection = self.stage('low-inspection-must-not-move', 'inspect_support("green cube", "red cube")', 'failed')
        acquisition = low_inspection.get('evidence_acquisition', {})
        if acquisition.get('reason') != 'retract_before_lateral_inspection' or acquisition.get('views_used') != 0:
            raise RuntimeError('Low inspection did not defer to vertical retreat')
        return recovery

    def interrupted_retreat(self, recovery, mode):
        old_revision = self.e.revision
        code = f'goto_pose({recovery["target_xyz"]!r}, {recovery["target_wxyz"]!r})'
        request = 'interruptible-retreat'
        self.e.decision(request, old_revision)
        sid = self.e.execute(request, old_revision, request, code)
        wait_for(lambda: self.e.motion or self.e.active is None, 20)
        if self.e.active is None:
            raise RuntimeError('Retreat finished before interrupt; interruption coverage missing')
        if mode == 'cancel_retreat':
            stop = self.e.cancel()
        else:
            stop = self.e.update_goal(dict(holding='green cube'))
        wait_for(lambda: self.e.active is None, 10)
        result = copy.deepcopy(self.e.segments[sid])
        self.segments.append(result)
        if result['execution'] != 'cancelled':
            raise RuntimeError('Interrupted retreat was not cancelled')
        count = sum(event['kind'] == 'api_started' for event in self.e.events)
        if self.e.execute(request, old_revision, request, code) != sid:
            raise RuntimeError('Repeated request was not idempotent')
        stale_rejected = None
        if mode == 'change_goal_retreat':
            try:
                self.e.execute('late-old-code', old_revision, 'late', code)
            except StaleRevision:
                stale_rejected = True
            else:
                raise RuntimeError('Old revision accepted after goal change')
        time.sleep(.4)
        if sum(event['kind'] == 'api_started' for event in self.e.events) != count:
            raise RuntimeError('Late action appeared after interrupted retreat')
        self.e.event('diagnostic_interrupt_checked', mode=mode, stop=stop,
                     old_revision=old_revision, stale_rejected=stale_rejected,
                     before_api_count=count, after_api_count=count)


def source_hashes():
    root = Path(__file__).resolve().parents[1]
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ['agentic_cap', 'experiments'] for p in sorted((root/folder).glob('*.py'))}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--upstream', required=True)
    p.add_argument('--records', type=Path, required=True)
    args = p.parse_args()
    root = args.records.resolve()
    root.mkdir(parents=True, exist_ok=False)
    os.environ.update(CAP_ACTIVE_EVIDENCE='1', CAP_PLACEMENT_EVIDENCE='1', CAP_SUPPORT_OCCLUSION='')
    hashes = source_hashes()
    protocol = dict(cases=CASES, task='cube_stack', seed=62, model=None, settle_seconds=1.25,
                    source_sha256=hashes, runtime=runtime_provenance('cap', args.upstream),
                    scope='synthetic post-descent support RGB-D mask; fixed driver; recovery coverage, not benchmark')
    (root/'protocol.json').write_text(json.dumps(protocol, indent=2))
    rows = []
    for index, mode in enumerate(CASES):
        if source_hashes() != hashes:
            raise RuntimeError('Frozen source changed')
        manager = Manager(root, lambda e: DescentFaultBackend(e, args.upstream, mode))
        state = manager.create(f'case-{index}', 'cube_stack', 62,
                               dict(on=['red cube', 'green cube'], empty_gripper=True))
        e = manager.episodes[state['id']]
        trial = RetreatTrial(e, 'support_active')
        row = dict(mode=mode, episode_id=e.id, diagnosis_passed=False)
        try:
            wait_for(lambda: e.snapshot or e.status == 'failed', 150)
            if e.status == 'failed':
                failure = next((event for event in reversed(e.events) if event['kind'] == 'runtime_failed'), {})
                raise RuntimeError('Runtime initialization failed: '+failure.get('error', 'unknown'))
            trial.measure('initial')
            recovery = trial.reach_low_pose()
            row['recovery'] = recovery
            if mode in {'cancel_retreat', 'change_goal_retreat'}:
                trial.interrupted_retreat(recovery, mode)
            else:
                trial.stage('recorded-retreat', f'goto_pose({recovery["target_xyz"]!r}, {recovery["target_wxyz"]!r})')
                inspected = trial.stage('high-inspection', 'inspect_support("green cube", "red cube")',
                                        'failed' if mode == 'persistent_unknown' else 'completed')
                row['after_retreat_inspection'] = inspected.get('evidence_acquisition')
                if mode == 'persistent_unknown':
                    if row['after_retreat_inspection'].get('status') != 'unknown':
                        raise RuntimeError('Persistent occlusion was not unknown')
                    trial.stage('still-cannot-release', 'open_gripper()', 'failed')
                else:
                    if row['after_retreat_inspection'].get('status') != 'available':
                        raise RuntimeError('Retreat did not restore support evidence')
                    trial.stage('holding-preserved', 'check_holding("red cube")')
            row['diagnosis_passed'] = True
        except Exception as exc:
            row['error'] = str(exc)
        finally:
            time.sleep(protocol['settle_seconds'])
            row.update(public_final=e.finish(e.revision), segments=trial.segments)
            e.close()
            row['episode_state'] = e.state()
            rows.append(row)
            (root/'results.json').write_text(json.dumps(rows, indent=2))
            print(json.dumps(dict(mode=mode, episode_id=e.id, passed=row['diagnosis_passed'],
                                  error=row.get('error'))), flush=True)
    if not all(row['diagnosis_passed'] for row in rows):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
