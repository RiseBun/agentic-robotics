"""Classify public evidence without inventing a physical cause or reading truth."""
from .interpreter import BoundaryError


class EffectError(RuntimeError):
    def __init__(self, result, phase='execution'):
        self.result, self.phase = result, phase
        super().__init__('Public effect ' + result['status'])


class MotionError(RuntimeError):
    def __init__(self, evidence):
        self.evidence = evidence
        super().__init__('Motion target not reached: ' + evidence.get('reason', 'motion_budget_exhausted'))


def feedback(exc, *, phase='execution', call_id=None, api=None, observation=None):
    """Categories describe observed failure modes, not their unobserved causes."""
    code = getattr(exc, 'code', None)
    evidence = getattr(exc, 'result', getattr(exc, 'evidence', None))
    if isinstance(exc, EffectError):
        phase = exc.phase
        category = 'insufficient_evidence' if exc.result['status'] == 'unknown' else 'condition_not_satisfied'
    elif isinstance(exc, MotionError):
        category = 'motion_target_not_reached'
    elif code == 'INSUFFICIENT_STAGE_BUDGET':
        category = 'resources_insufficient'
        phase = 'program_preflight'
    elif code == 'BUDGET_EXHAUSTED':
        category = 'budget_exhausted'
    elif code == 'PUBLIC_HOLDING_LOSS':
        category = 'public_holding_loss'
    elif code == 'CANCELLED':
        category = 'cancelled'
    elif code == 'EPISODE_UNAVAILABLE':
        category = 'episode_unavailable'
    elif isinstance(exc, (BoundaryError, SyntaxError, KeyError)) and not api:
        category = 'invalid_program'
    elif isinstance(exc, TimeoutError):
        category = 'interface_timeout'
    else:
        category = 'api_exception' if api else 'program_exception'
    observation = observation or {}
    return dict(category=category, phase=phase, api=api, call_id=call_id,
                message=str(exc), evidence=evidence,
                observation=dict(evidence_ref=observation.get('evidence_ref'),
                                 captured_at=observation.get('captured_at'),
                                 control_step=observation.get('control_step')),
                causal_status='not_established')
