"""Fault during a model-generated segment's delayed public RPC, not idle thinking.

The controller loop keeps running. No diagnostic API is exposed to the model.
This diagnosis does not establish slip detection during uninterrupted arm motion.
"""
import argparse
import json
import os
import time
from pathlib import Path

from agentic_cap.cap_backend import CaPBackend
from agentic_cap.core import Manager
from agentic_cap.server import runtime_provenance, serve


class ActiveHoldingBackend(CaPBackend):
    def __init__(self, episode, upstream):
        super().__init__(episode, upstream, evaluation=True)
        self.verified_segment = None
        self.fault_segment = None
        self.release_started_step = None
        self.release_applied_steps = 0

    def arm_holding_watchdog(self, name, snapshot):
        super().arm_holding_watchdog(name, snapshot)
        if name == 'red cube':
            self.verified_segment = self.episode.active

    def tools(self, segment):
        tools = super().tools(segment)

        def wrap(name, function):
            def delayed(*args, **kwargs):
                e = self.episode
                with e.cv:
                    e.checkpoint(segment)
                    eligible = (self.fault_segment is None and e.active == segment['id']
                        and self.verified_segment is not None and self.verified_segment != segment['id']
                        and self.held_hint == 'red cube' and e.gripper < .5
                        and getattr(self, 'holding_results', {}).get('red cube') == 'verified')
                    if eligible:
                        self.fault_segment = segment['id']
                        e.event('diagnostic_api_delay_started', api=name, delay_seconds=5,
                                scope='public API latency in active generated code; no control-loop delay')
                        end = time.monotonic()+5
                        try:
                            while time.monotonic() < end:
                                e.checkpoint(segment)
                                e.cv.wait(.05)
                            e.checkpoint(segment)
                            e.event('diagnostic_api_delay_finished', api=name)
                            e.event('diagnostic_delayed_api_dispatched', api=name, original_segment_id=segment['id'])
                        except Exception:
                            e.event('diagnostic_api_delay_abandoned', original_segment_id=segment['id'], api=name)
                            raise
                return function(*args, **kwargs)
            return delayed

        for name in ['get_execution_state', 'get_object_pose', 'goto_pose', 'home_pose']:
            if name in tools:
                tools[name] = wrap(name, tools[name])
        return tools

    def step(self, target, gripper):
        if (self.release_started_step is None and self.fault_segment is not None
                and self.episode.active == self.fault_segment and gripper < .5):
            self.release_started_step = self.tick
            self.episode.event('diagnostic_actuator_fault_started', control_step=self.tick,
                scope='one thirty-step actuator release during delayed public API in active code')
        if self.release_started_step is not None and self.release_applied_steps < 30:
            self.release_applied_steps += 1
            super().step(target, 1.)
            if self.release_applied_steps == 30:
                self.episode.event('diagnostic_actuator_fault_finished', control_step=self.tick, applied_steps=30)
        else:
            super().step(target, gripper)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--upstream', required=True)
    p.add_argument('--records', type=Path, required=True)
    p.add_argument('--port', type=int, required=True)
    args = p.parse_args()
    root = args.records.resolve()
    root.mkdir(parents=True, exist_ok=False)
    os.environ.update(CAP_HOLDING_WATCHDOG='1', CAP_ACTIVE_EVIDENCE='1', CAP_PLACEMENT_EVIDENCE='1',
                      CAP_SUPPORT_SECOND_VIEW='robot0_eye_in_hand', CAP_SUPPORT_OCCLUSION='')
    manager = Manager(root, lambda e: ActiveHoldingBackend(e, args.upstream))
    provenance = runtime_provenance('cap', args.upstream)
    provenance['features']['diagnostic_fault'] = 'active_rpc'
    server = serve(manager, '127.0.0.1', args.port, os.environ['CAP_RUNTIME_TOKEN'], provenance)
    print(json.dumps(dict(listening=args.port, mode='active_rpc')), flush=True)
    try:
        server.serve_forever()
    finally:
        for episode in manager.episodes.values():
            episode.close()
        server.server_close()


if __name__ == '__main__':
    main()
