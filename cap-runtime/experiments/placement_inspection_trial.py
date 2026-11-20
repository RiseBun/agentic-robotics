"""Real-CaP engineering diagnosis with explicit post-release sensor faults.

Not a benchmark: the two frozen cases use the already-seen stack seed 62.
Truth is read only by a separate scorer after both episodes close.
"""
import argparse
import json
import os
import time
from pathlib import Path

from agentic_cap.cap_backend import CaPBackend
from agentic_cap.core import Manager
from agentic_cap.server import runtime_provenance
from experiments.active_view_trial import wait_for
from experiments.runtime_support_trial import RuntimeSupportTrial


class PlacementTrial(RuntimeSupportTrial):
    def place(self, measurement, name, support, aside=False):
        # Explicitly recorded clearance for this diagnosis; the historic
        # controller and frozen regressions keep their original 8 cm retreat.
        obj, held_method = self.center(measurement, name)
        target, support_method = self.center(measurement, support)
        import numpy as np
        offset = obj-np.asarray(measurement['tcp_xyz'])
        prior = measurement['objects'][support]['size_reference']
        size = prior['side_m'] if prior else max(measurement['objects'][support]['visible']['surface_span_xyz'][:2])
        target[2] += size
        tcp, q = (target-offset).tolist(), measurement['tcp_wxyz']
        retreat = [tcp[0], tcp[1], tcp[2]+.14]
        self.e.event('placement_geometry_selected', object_name=name, support=support,
                     held_method=held_method, support_method=support_method,
                     measured_offset_xyz=offset.tolist(), source_ref=measurement['evidence_ref'], retreat_m=.14)
        return self.segment('release-'+name,
            f'goto_pose({tcp!r}, {q!r}, z_approach=0.10)\nopen_gripper()\ngoto_pose({retreat!r}, {q!r})',
            dict(preconditions=[{'holding': name}], postcondition={'on': [name, support], 'empty_gripper': True}))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--upstream', required=True)
    p.add_argument('--records', type=Path, required=True)
    args = p.parse_args()
    root = args.records.resolve()
    root.mkdir(parents=True, exist_ok=False)
    os.environ.update(CAP_ACTIVE_EVIDENCE='1', CAP_PLACEMENT_EVIDENCE='1')
    cases = ['green_after_release_until_lateral_view', 'green_after_release_persistent']
    manifest = dict(cases=cases, task='cube_stack', seed=62, model=None, retreat_m=.14,
                    settle_seconds=1.25, scope='synthetic post-release RGB-D fault; not natural occlusion or success rate')
    (root/'protocol.json').write_text(json.dumps(manifest, indent=2))
    manager = Manager(root, lambda e: CaPBackend(e, args.upstream, evaluation=True))
    rows = []
    for index, mode in enumerate(cases):
        os.environ['CAP_SUPPORT_OCCLUSION'] = mode
        state = manager.create(f'case-{index}', 'cube_stack', 62,
                               dict(on=['red cube', 'green cube'], empty_gripper=True))
        e = manager.episodes[state['id']]
        trial = PlacementTrial(e, 'support_active')
        result = dict(mode=mode, episode_id=e.id, runtime=runtime_provenance('cap', args.upstream))
        try:
            wait_for(lambda: e.snapshot or e.status == 'failed', 150)
            e.decision('initial', e.revision)
            trial.measure('initial')
            held = trial.pick('red cube')
            held = trial.prepare_placement(held, 'red cube', 'green cube')
            trial.place(held, 'red cube', 'green cube')
            result['before_inspection'] = e.finish(e.revision)
            trial.segment('inspect-placement', 'inspect_placement("red cube", "green cube")')
        except Exception as exc:
            result['error'] = str(exc)
        finally:
            time.sleep(1.25)
            result.update(public_final=e.finish(e.revision), segments=trial.segments,
                          used=e.state()['used'])
            e.close()
            result['closed'] = e.state()['status']
        rows.append(result)
        (root/'results.json').write_text(json.dumps(rows, indent=2))
        print(json.dumps(dict(mode=mode, episode_id=e.id, public=result['public_final']['status'],
                              error=result.get('error'))), flush=True)


if __name__ == '__main__':
    main()
