"""Target-aware support acquisition. Fixed-controller experiment only.

The span gate is a cube-specific development heuristic, not calibrated pose
confidence. It identifies absent/thin support geometry without consulting truth.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np
from experiments.active_view_trial import PublicTrial, PROTOCOL as BASE_PROTOCOL, run_plan


PROTOCOL = dict(BASE_PROTOCOL, inspection='support visibility, not held-object yaw',
                support_minimum_horizontal_span_fraction=.4,
                inspection_translation_y_m=.10, inspection_maximum_abs_y_m=.22,
                maximum_support_views=2,
                support_gate_scope='red/green cubes and fixed central camera; uncalibrated coverage rule')


def support_evidence(measurement, support):
    obj = measurement['objects'].get(support, {})
    visible = obj.get('visible', {})
    result = dict(status='unknown', object_name=support, evidence_ref=measurement.get('evidence_ref'),
                  scope=PROTOCOL['support_gate_scope'])
    if visible.get('status') != 'visible':
        return dict(result, reason='support_not_visible')
    if obj.get('candidate', {}).get('status') == 'estimated':
        return dict(result, status='available', reason='three_face_support_estimate')
    spans = np.asarray(visible.get('surface_span_xyz', []), dtype=float)
    if spans.shape != (3,) or not np.isfinite(spans).all() or np.any(spans <= 0):
        return dict(result, reason='invalid_visible_spans')
    prior = obj.get('size_reference') or {}
    scale = max(float(spans.max()), float(prior.get('side_m', 0)))
    ratio = float(min(spans[:2])/scale)
    result.update(horizontal_span_ratio=ratio, observed_scale_m=scale,
                  visible_depth_pixels=visible.get('valid_depth_pixels'))
    if ratio < PROTOCOL['support_minimum_horizontal_span_fraction']:
        return dict(result, reason='support_horizontal_extent_truncated')
    return dict(result, status='available', reason='visible_box_support; not_center_verified')


def observation_translations(tcp_xyz):
    original = np.asarray(tcp_xyz, dtype=float)
    if original.shape != (3,) or not np.isfinite(original).all():
        raise ValueError('Invalid public TCP')
    # Try toward the base midline first to avoid increasing lateral reach.
    first = -1 if original[1] > 0 else 1
    targets = []
    for direction in [first, -first]:
        target = original.copy()
        target[1] += direction*PROTOCOL['inspection_translation_y_m']
        if abs(target[1]) <= PROTOCOL['inspection_maximum_abs_y_m']:
            targets.append(target.tolist())
    return targets[:PROTOCOL['maximum_support_views']]


class PlacementViewTrial(PublicTrial):
    def placement_evidence(self, measurement, name, support):
        return dict(held_center=measurement['objects'][name]['candidate'],
                    support=support_evidence(measurement, support))

    def prepare_placement(self, measurement, name, support):
        evidence = self.placement_evidence(measurement, name, support)
        self.e.event('placement_evidence_checked', mode=self.mode, evidence=evidence)
        if self.mode == 'passive' or evidence['support']['status'] == 'available':
            return measurement
        if self.mode != 'support_active':
            raise ValueError('Unsupported placement-view experiment mode')
        q = measurement['tcp_wxyz']
        for index, xyz in enumerate(observation_translations(measurement['tcp_xyz'])):
            self.inspections += 1
            measurement = self.segment(f'inspect-support-{support}-{index+1}',
                f'goto_pose({xyz!r}, {q!r})\ncheck_holding({name!r})',
                dict(preconditions=[{'holding': name}], postcondition={'holding': name}))
            evidence = self.placement_evidence(measurement, name, support)
            self.e.event('placement_evidence_checked', mode=self.mode, evidence=evidence)
            if evidence['support']['status'] == 'available':
                return measurement
        raise ValueError('Support remains unobservable after bounded inspection')


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--upstream', required=True)
    p.add_argument('--records', type=Path, required=True)
    p.add_argument('--plan', type=Path, required=True)
    run_plan(p.parse_args(), PlacementViewTrial, PROTOCOL)


if __name__ == '__main__':
    main()
