"""Seen-failure visibility diagnosis. No LLM or release at an unknown support.

Compare existing calibrated RGB-D cameras at the same physical step. Fixed
public geometry selects the approach; no object truth is used by this driver.
"""
import argparse
import copy
import json
import os
import time
from pathlib import Path
from agentic_cap.core import Manager
from agentic_cap.cap_backend import CaPBackend
from agentic_cap.active_evidence import support_evidence
from agentic_cap.public_views import ALLOWED_CAMERAS, read_camera_packet, save_camera_packet
from experiments.active_view_trial import wait_for
from experiments.vertical_retreat_trial import RetreatTrial, source_hashes


class ProbeBackend(CaPBackend):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.probe_request = None
        self.probe_results = {}
        self.probe_references = {}

    def capture(self):
        value = super().capture()
        label = self.probe_request
        if label and label not in self.probe_results:
            started = time.monotonic()
            views = {}
            self.episode.event('camera_probe_started', label=label, control_step=self.tick)
            for name in ALLOWED_CAMERAS:
                try:
                    packet = read_camera_packet(self, name)
                    packet.update(control_step=self.tick, captured_at=time.time(), simulation_time=self.sim_time(),
                                  gripper_command=self.episode.gripper)
                    view = save_camera_packet(self.episode.directory, 'view-'+label+'-'+name, packet)
                    view['support_evidence'] = support_evidence(view, 'green cube', self.probe_references.get(name))
                    if label == 'initial':
                        self.probe_references[name] = copy.deepcopy(view)
                    views[name] = view
                except Exception as exc:
                    views[name] = dict(status='unknown', reason=str(exc))
            result = dict(label=label, control_step=self.tick, views=views,
                          elapsed_seconds=time.monotonic()-started)
            self.episode.event('camera_probe_finished', **result)
            self.probe_results[label] = result
        return value


def request_probe(e, label):
    with e.lock:
        e.backend.probe_request = label
    wait_for(lambda: label in e.backend.probe_results, 15)
    return copy.deepcopy(e.backend.probe_results[label])


def main():
    import numpy as np
    p = argparse.ArgumentParser()
    p.add_argument('--upstream', required=True)
    p.add_argument('--records', type=Path, required=True)
    args = p.parse_args()
    root = args.records.resolve()
    root.mkdir(parents=True, exist_ok=False)
    os.environ.update(CAP_ACTIVE_EVIDENCE='1', CAP_PLACEMENT_EVIDENCE='1', CAP_SUPPORT_OCCLUSION='')
    hashes = source_hashes()
    (root/'protocol.json').write_text(json.dumps(dict(task='cube_restack', seed=66, cameras=ALLOWED_CAMERAS,
        source_sha256=hashes, sensor_fault=None, controller='fixed public geometry, no LLM',
        scope='reproduce seen release-position visibility; no release or task-success claim'), indent=2))
    manager = Manager(root, lambda e: ProbeBackend(e, args.upstream, evaluation=True))
    state = manager.create('release-camera-probe-restack66', 'cube_restack', 66,
                           dict(on=['red cube', 'green cube'], empty_gripper=True))
    e = manager.episodes[state['id']]
    result = dict(episode_id=e.id, status='failed')
    try:
        wait_for(lambda: e.snapshot or e.status == 'failed', 150)
        if e.status != 'ready': raise RuntimeError('Backend initialization failed')
        trial = RetreatTrial(e, 'support_active')
        result['initial'] = request_probe(e, 'initial')
        trial.measure('initial')
        measurement = trial.pick('green cube')
        trial.place(measurement, 'green cube', 'red cube', aside=True)
        measurement = trial.pick('red cube')
        measurement = trial.prepare_placement(measurement, 'red cube', 'green cube')
        obj, _ = trial.center(measurement, 'red cube')
        support, _ = trial.center(measurement, 'green cube')
        prior = measurement['objects']['green cube']['size_reference']
        size = prior['side_m'] if prior else max(measurement['objects']['green cube']['visible']['surface_span_xyz'][:2])
        support[2] += size
        low = (support-(obj-np.asarray(measurement['tcp_xyz']))).tolist()
        high = [low[0], low[1], low[2]+.12]
        q = measurement['tcp_wxyz']
        e.event('diagnostic_public_release_pose_selected', evidence_ref=measurement['evidence_ref'], target_xyz=low, target_wxyz=q)
        trial.stage('reach-release-origin', f'goto_pose({high!r}, {q!r})')
        measured = e.observe()
        target = list(measured['tcp_xyz']); target[2] = low[2]
        trial.stage('reach-release-height', f'goto_pose({target!r}, {measured["tcp_wxyz"]!r}, z_approach=0.0)')
        result['release_position'] = request_probe(e, 'release')
        result.update(status='captured', gripper_command=e.gripper, used=e.state()['used'])
        if source_hashes() != hashes: raise RuntimeError('Frozen probe source changed')
    except Exception as exc:
        result['error'] = str(exc)
    finally:
        e.close()
        result['closed'] = e.state()['status']
        result['used_at_close'] = e.state()['used']
        result['source_unchanged'] = source_hashes() == hashes
        (root/'result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(dict(status=result['status'], error=result.get('error'),
        views={k:v.get('support_evidence', {'status':'unknown'}) for k,v in result.get('release_position',{}).get('views',{}).items()})), flush=True)
    if result['status'] != 'captured': raise SystemExit(1)


if __name__ == '__main__':
    main()
