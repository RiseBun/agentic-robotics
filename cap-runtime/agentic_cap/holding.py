"""Three-state development rule using public surface and robot observations.

Supported means consistent with holding during the observed window. It is
neither contact truth nor a guarantee about a subsequent move.
"""
from __future__ import annotations
import numpy as np

HOLDING_PROTOCOL = {
    'minimum_observed_lift_m': .04,
    'maximum_surface_tcp_distance_m': .10,
    'object_near_initial_height_m': .03,
    'tcp_above_initial_object_m': .08,
    'maximum_offset_drift_m': .02,
    'observation_window_steps': 20,
    'observation_interval_steps': 5,
}


def holding_evidence(reference, samples, object_name='red cube'):
    baseline = reference['objects'].get(object_name, {})
    result = {'state': 'unknown', 'object_name': object_name,
              'scope': 'observed_window_only; no_contact_sensor_or_future_stability_guarantee',
              'samples': []}
    if baseline.get('status') != 'visible':
        return dict(result, reason='reference_not_visible')
    base = np.array(baseline['surface_median_xyz'])
    offsets=[]
    states=[]
    for sample in samples:
        obj = sample['objects'].get(object_name, {})
        row = {'control_step': sample.get('control_step')}
        if obj.get('status') != 'visible':
            row.update(evidence='unknown', reason='object_not_visible')
            states.append('unknown')
            result['samples'].append(row)
            continue
        surface = np.array(obj['surface_median_xyz'])
        tcp = np.array(sample['tcp_xyz'])
        lift = float(surface[2]-base[2])
        distance = float(np.linalg.norm(surface-tcp))
        tcp_height = float(tcp[2]-base[2])
        evidence = 'unknown'
        if lift >= .04 and distance <= .10 and sample.get('last_gripper_command') == 'closed':
            evidence = 'holding_supported'
        elif abs(lift) <= .03 and tcp_height >= .08:
            evidence = 'not_held_supported'
        row.update(evidence=evidence, visible_surface_lift_m=lift,
                   surface_tcp_distance_m=distance, tcp_height_above_reference_m=tcp_height)
        result['samples'].append(row)
        states.append(evidence)
        offsets.append(surface-tcp)
    if not samples:
        return dict(result, reason='no_samples')
    if all(s=='not_held_supported' for s in states):
        return dict(result, state='violated', reason='object_remains_low_while_tcp_is_high')
    timed = [s.get('control_step') for s in samples]
    window = 0 if any(t is None for t in timed) else timed[-1]-timed[0]
    result['window_control_steps'] = window
    if all(s=='holding_supported' for s in states):
        drift=float(np.max(np.linalg.norm(np.array(offsets)-offsets[0], axis=1)))
        result['maximum_relative_surface_drift_m']=drift
        if window >= 20 and drift <= .02:
            # A tilted grasp need not put the object centre at the TCP. Preserve
            # that measured offset when translating a held cube for placement.
            # This remains a public visible-box estimate, not contact truth.
            centers = []
            for sample in samples:
                obj = sample['objects'][object_name]
                if 'surface_q05_xyz' in obj and 'surface_q95_xyz' in obj:
                    center = (np.array(obj['surface_q05_xyz'])+np.array(obj['surface_q95_xyz']))/2
                    centers.append(center-np.array(sample['tcp_xyz']))
            if len(centers) == len(samples):
                result['estimated_center_tcp_offset_xyz'] = np.median(centers,axis=0).tolist()
                result['reference_tcp_wxyz'] = samples[-1].get('tcp_wxyz')
                result['placement_scope'] = 'translation only; preserve this TCP orientation; visible cube geometry'
            return dict(result,state='satisfied',reason='elevated_near_tcp_and_consistent_during_observed_window')
        return dict(result,reason='insufficient_duration_or_relative_consistency')
    return dict(result,reason='mixed_or_insufficient_public_evidence')
