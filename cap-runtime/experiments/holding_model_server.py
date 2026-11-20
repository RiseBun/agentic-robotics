"""Natural-task model diagnosis; one automatic actuator fault after public hold.

Product routes and model tools are unchanged. The experiment injects a finite
actuator mismatch once, using only public verified-hold/command/idle state.
Persistent red sensor loss, if selected, begins only AFTER monitor-confirmed hold.
"""
import argparse
import json
import os
from pathlib import Path

from agentic_cap.cap_backend import CaPBackend
from agentic_cap.core import Manager
from agentic_cap.server import runtime_provenance, serve


class HoldingModelBackend(CaPBackend):
    def __init__(self, episode, upstream, mode):
        super().__init__(episode, upstream, evaluation=True)
        self.diagnostic_mode = mode
        self.release_scheduled_step = None
        self.release_started_step = None
        self.release_applied_steps = 0
        self.red_mask_announced = False

    def step(self, target, gripper):
        e = self.episode
        if (self.release_scheduled_step is None and e.active is None and gripper < .5
                and self.held_hint == 'red cube'
                and getattr(self, 'holding_results', {}).get('red cube') == 'verified'):
            self.release_scheduled_step = self.tick
            e.event('diagnostic_release_scheduled', control_step=self.tick, delay_steps=10,
                    scope='once after public verified hold and first idle phase')
        if (self.release_started_step is None and self.release_scheduled_step is not None
                and self.tick >= self.release_scheduled_step+10 and gripper < .5):
            self.release_started_step = self.tick
            e.event('diagnostic_actuator_fault_started', control_step=self.tick,
                    scope='desired closed; actual actuator open for thirty steps; no object truth')
        if self.release_started_step is not None and self.release_applied_steps < 30:
            self.release_applied_steps += 1
            super().step(target, 1.)
            if self.release_applied_steps == 30:
                e.event('diagnostic_actuator_fault_finished', control_step=self.tick, applied_steps=30)
        else:
            super().step(target, gripper)

    def apply_camera_fault(self, rgb, depth):
        alert = self.episode.runtime_alert
        # Latch the sensor fault once the public monitor's protective hold is
        # confirmed; a later normal release cannot silently restore the camera.
        if (self.diagnostic_mode == 'occluded' and alert is not None and alert['hold_confirmed']
                and not self.red_mask_announced):
            self.red_mask_announced = True
            self.episode.event('diagnostic_red_sensor_fault_started', monitor_event_id=alert['event_id'])
        if self.red_mask_announced:
            from agentic_cap.public_sensors import mask_public_support
            return mask_public_support(rgb, depth, 'red cube')
        return super().apply_camera_fault(rgb, depth)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--upstream', required=True)
    p.add_argument('--records', type=Path, required=True)
    p.add_argument('--port', type=int, required=True)
    p.add_argument('--mode', choices=['recoverable', 'occluded'], required=True)
    args = p.parse_args()
    root = args.records.resolve()
    root.mkdir(parents=True, exist_ok=False)
    os.environ.update(CAP_HOLDING_WATCHDOG='1', CAP_ACTIVE_EVIDENCE='1', CAP_PLACEMENT_EVIDENCE='1',
                      CAP_SUPPORT_SECOND_VIEW='robot0_eye_in_hand', CAP_SUPPORT_OCCLUSION='')
    manager = Manager(root, lambda e: HoldingModelBackend(e, args.upstream, args.mode))
    provenance = runtime_provenance('cap', args.upstream)
    provenance['features']['diagnostic_fault'] = args.mode
    provenance['diagnostic'] = dict(type='holding-model', mode=args.mode,
        scope='one injected actuator release after public verified hold; no reset/preparation/controller handoff')
    server = serve(manager, '127.0.0.1', args.port, os.environ['CAP_RUNTIME_TOKEN'], provenance)
    print(json.dumps(dict(listening=args.port, mode=args.mode)), flush=True)
    try: server.serve_forever()
    finally:
        for episode in manager.episodes.values(): episode.close()
        server.server_close()


if __name__ == '__main__': main()
