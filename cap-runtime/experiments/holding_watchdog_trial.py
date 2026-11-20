"""Physical diagnosis of public monitoring, with explicitly injected faults.

No object truth enters the controller. Fault one deliberately applies an open
actuator command despite the recorded desired closed command, producing a real
release; this is an actuator-fault test, not a naturally occurring slip claim.
Fault two masks public red RGB-D pixels without changing physical state.
"""
import argparse
import copy
import json
import os
import time
from pathlib import Path

from agentic_cap.cap_backend import CaPBackend
from agentic_cap.core import Manager, StaleRevision
from experiments.active_view_trial import wait_for
from experiments.vertical_retreat_trial import RetreatTrial, source_hashes


class WatchDiagnosticBackend(CaPBackend):
    def __init__(self, episode, upstream):
        super().__init__(episode, upstream, evaluation=True)
        self.diagnostic_fault = ''
        self.injected_steps = 0

    def step(self, target, gripper):
        if self.diagnostic_fault == 'actuator_release' and self.injected_steps < 30:
            if self.injected_steps == 0:
                self.episode.event('diagnostic_actuator_fault_started',
                    scope='desired closed command; inject open actuator for thirty control steps')
            self.injected_steps += 1
            super().step(target, 1.)
        else:
            super().step(target, gripper)

    def apply_camera_fault(self, rgb, depth):
        if self.diagnostic_fault == 'red_pixels_missing':
            from agentic_cap.public_sensors import mask_public_support
            return mask_public_support(rgb, depth, 'red cube')
        return super().apply_camera_fault(rgb, depth)

    def tools(self, segment):
        tools = super().tools(segment)
        def diagnostic_wait():
            # A deliberately long active API wait: physics and watcher continue.
            e = self.episode
            with e.cv:
                start = self.tick
                self.diagnostic_fault = segment['subgoal']
                e.event('diagnostic_fault_activated', fault=self.diagnostic_fault, control_step=start)
                while self.tick-start < 110:
                    e.checkpoint(segment)
                    e.cv.wait(.02)
            return dict(status='verified', scope='wait completed, not task success')
        def forbidden_late_action():
            self.episode.event('diagnostic_late_action_executed')
            return dict(status='verified')
        tools.update(diagnostic_wait=diagnostic_wait, diagnostic_late_action=forbidden_late_action)
        return tools


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--upstream', required=True)
    p.add_argument('--records', type=Path, required=True)
    args = p.parse_args()
    root = args.records.resolve()
    root.mkdir(parents=True, exist_ok=False)
    os.environ.update(CAP_HOLDING_WATCHDOG='1', CAP_ACTIVE_EVIDENCE='1', CAP_PLACEMENT_EVIDENCE='1',
                      CAP_SUPPORT_SECOND_VIEW='', CAP_SUPPORT_OCCLUSION='')
    hashes = source_hashes()
    modes = ['no_fault', 'actuator_release', 'red_pixels_missing']
    (root/'protocol.json').write_text(json.dumps(dict(modes=modes, task='cube_lifting', seed=69,
        source_sha256=hashes, model_used=False, cancellation='during active API wait, stationary held target',
        scope='injected actuator/sensor faults; no natural slip, collision or real-hardware claim'), indent=2))
    results = []
    for mode in modes:
        manager = Manager(root/mode, lambda e: WatchDiagnosticBackend(e, args.upstream))
        state = manager.create('watch-'+mode, 'cube_lifting', 69, dict(holding='red cube'))
        e = manager.episodes[state['id']]
        row = dict(mode=mode, episode_id=e.id, passed=False)
        try:
            wait_for(lambda: e.snapshot or e.status == 'failed', 150)
            if e.status != 'ready': raise RuntimeError('Initialization failed')
            trial = RetreatTrial(e, 'support_active')
            trial.measure('initial')
            trial.pick('red cube')
            if not e.backend.holding_watchdog.arm_record:
                raise RuntimeError('Verified public grasp did not arm monitor')
            old_revision = e.revision
            e.decision('fault', old_revision)
            sid = e.execute('fault', old_revision, mode, 'diagnostic_wait()\ndiagnostic_late_action()')
            wait_for(lambda: e.active is None, 15)
            row['segment'] = copy.deepcopy(e.segments[sid])
            if mode == 'actuator_release':
                wait_for(lambda: e.runtime_alert is not None and e.runtime_alert['hold_confirmed'], 5)
                if e.segments[sid]['execution'] != 'cancelled': raise RuntimeError('Old code was not cancelled')
                if any(event['kind'] == 'diagnostic_late_action_executed' for event in e.events):
                    raise RuntimeError('Late action bypassed interruption')
                try: e.execute('obsolete', old_revision, 'late', 'diagnostic_late_action()')
                except StaleRevision: row['old_decision_rejected'] = True
                else: raise RuntimeError('Old model revision accepted')
                row['alert'] = copy.deepcopy(e.runtime_alert)
            else:
                if e.runtime_alert is not None or e.segments[sid]['execution'] != 'completed':
                    raise RuntimeError('False monitor interruption')
            time.sleep(1.25)
            row['public_final'] = e.finish(e.revision)
            expected = 'verified' if mode == 'no_fault' else 'failed' if mode == 'actuator_release' else 'unknown'
            if row['public_final']['status'] != expected:
                raise RuntimeError('Unexpected public final status: '+row['public_final']['status'])
            row['passed'] = True
        except Exception as exc:
            row['error'] = str(exc)
        finally:
            e.close()
            row.update(closed=e.state()['status'], used=e.state()['used'], source_unchanged=source_hashes()==hashes)
            results.append(row)
            (root/'results.json').write_text(json.dumps(results, indent=2))
            print(json.dumps({k:row.get(k) for k in ['mode','episode_id','passed','error','public_final']}), flush=True)
    if not all(r['passed'] and r['source_unchanged'] for r in results): raise SystemExit(1)


if __name__ == '__main__': main()
