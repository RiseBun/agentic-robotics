"""Frozen fixed-controller regression using the registered inspect_support API.

The same public geometry controller and release guard are used in both groups.
Only the support_active group may call the skill. No private scoring is read
during execution. Synthetic camera-fault trials must be reported separately.
"""
import argparse
from pathlib import Path

from agentic_cap.active_evidence import support_evidence
from experiments.active_view_trial import PublicTrial, PROTOCOL as BASE_PROTOCOL, run_plan

PROTOCOL = dict(BASE_PROTOCOL, skill='inspect_support(green cube, red cube)',
                maximum_support_views=2, modes=['passive', 'support_active'],
                comparator='same Runtime/guards/controller; candidate may invoke registered skill')


class RuntimeSupportTrial(PublicTrial):
    def placement_evidence(self, measurement, name, support):
        return dict(held_center=measurement['objects'][name]['candidate'],
                    support=support_evidence(self.e.observe(), support, self.e.backend.reference))

    def prepare_placement(self, measurement, name, support):
        if self.mode == 'passive':
            return measurement
        if self.mode != 'support_active':
            raise ValueError('Unsupported Runtime evidence condition')
        evidence = self.placement_evidence(measurement, name, support)
        if evidence['support']['status'] == 'available':
            return measurement
        measured = self.segment('inspect-support', f'inspect_support({support!r}, {name!r})')
        self.inspections += self.segments[-1].get('evidence_acquisition', {}).get('views_used', 0)
        return measured


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--upstream', required=True)
    parser.add_argument('--records', type=Path, required=True)
    parser.add_argument('--plan', type=Path, required=True)
    run_plan(parser.parse_args(), RuntimeSupportTrial, PROTOCOL)


if __name__ == '__main__':
    main()
