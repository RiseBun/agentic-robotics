"""Independent, bounded public-evidence watcher, not a contact/slip oracle.

An already verified grasp arms this watcher. A complete *later* public window
must contradict that hold before it requests a protective interruption. Missing
objects, stale frames and uncertain low placement remain unknown.
"""
import copy
import math
import time

from .holding import HOLDING_PROTOCOL, holding_evidence


class HoldingWatchdog:
    def __init__(self, episode_id):
        self.episode_id = episode_id
        self.arm_record = None
        self.last_step = -1
        self.tripped = False

    def arm(self, name, snapshot):
        if name not in {'red cube', 'green cube'}:
            raise ValueError('Unsupported monitored object')
        step = snapshot.get('control_step')
        if (snapshot.get('episode_id') != self.episode_id or not isinstance(step, int)
                or isinstance(step, bool) or step < 0):
            return False
        # Repeating the same successful check cannot reset a pending window.
        if self.arm_record and self.arm_record['object_name'] == name and not self.tripped:
            return True
        if self.arm_record and self.tripped and step <= self.arm_record['control_step']:
            return False
        self.arm_record = dict(object_name=name, control_step=step,
                               evidence_ref=snapshot.get('evidence_ref'))
        self.last_step = step
        self.tripped = False
        return True

    def disarm(self):
        self.arm_record = None
        self.last_step = -1
        self.tripped = False

    def evaluate(self, reference, samples, snapshot, gripper_closed):
        result = dict(status='inactive', scope='public holding contradiction; physical cause not established')
        if not gripper_closed:
            self.disarm()
            return {**result, 'reason': 'commanded_release'}
        if self.arm_record is None:
            return {**result, 'reason': 'no_verified_grasp'}
        if self.tripped:
            return {**result, 'reason': 'already_interrupted'}
        step, stamp = snapshot.get('control_step'), snapshot.get('captured_at')
        number = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        if (snapshot.get('episode_id') != self.episode_id or not isinstance(step, int)
                or isinstance(step, bool) or step <= self.last_step or not number(stamp)
                or not 0 <= time.time()-stamp <= 1):
            return {**result, 'status': 'unknown', 'reason': 'stale_or_incoherent_public_snapshot'}
        self.last_step = step
        window_steps = HOLDING_PROTOCOL['observation_window_steps']
        lower = max(self.arm_record['control_step'], step - window_steps - 8)
        window = [s for s in samples if isinstance(s.get('control_step'), int)
                  and not isinstance(s.get('control_step'), bool) and lower <= s['control_step'] <= step]
        result.update(status='unknown', object_name=self.arm_record['object_name'],
                      arm=copy.deepcopy(self.arm_record), evidence_ref=snapshot.get('evidence_ref'),
                      captured_at=stamp, control_step=step)
        if (len(window) < 3 or window[-1]['control_step'] != step
                or window[-1]['control_step']-window[0]['control_step'] < window_steps):
            return {**result, 'reason': 'incomplete_post_arm_window'}
        previous, previous_stamp = None, None
        for sample in window:
            captured = sample.get('captured_at')
            current = sample['control_step']
            if (sample.get('episode_id') != self.episode_id or not number(captured)
                    or captured > stamp or stamp-captured > 2
                    or sample.get('last_gripper_command') != 'closed'
                    or (previous_stamp is not None and captured < previous_stamp)
                    or (previous is not None and not 0 < current-previous <= 10)):
                return {**result, 'reason': 'incoherent_or_gapped_public_window'}
            previous = current
            previous_stamp = captured
        if not isinstance(reference, dict):
            return {**result, 'reason': 'missing_public_reference'}
        try:
            evidence = holding_evidence(reference, window, self.arm_record['object_name'])
        except (KeyError, ValueError, TypeError):
            return {**result, 'reason': 'invalid_public_geometry'}
        result.update(evidence=evidence, evidence_refs=[s.get('evidence_ref') for s in window])
        if evidence['state'] == 'violated':
            self.tripped = True
            return {**result, 'status': 'violated', 'reason': evidence['reason']}
        return {**result, 'status': 'supported' if evidence['state'] == 'satisfied' else 'unknown',
                'reason': evidence['reason']}
