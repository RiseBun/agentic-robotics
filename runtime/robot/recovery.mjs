import crypto from 'node:crypto';

const digest = value => crypto.createHash('sha256').update(JSON.stringify(value)).digest('hex');
const codeKey = code => digest((code || '').replaceAll('\r\n', '\n').trim());
const actionKey = decision => decision.decision === 'run_skill'
  ? digest([decision.skill?.name, Object.entries(decision.skill?.arguments || {}).sort()]) : codeKey(decision.code);
const position = value => Array.isArray(value) && value.length === 3 && value.every(Number.isFinite) ? [...value] : null;
const status = value => value?.status || 'unknown';

// Only public, task-relevant facts enter this signature. Frame IDs, clock time,
// wording and pixel counts are excluded. Position changes use an explicit
// comparison tolerance, avoiding progress caused by crossing a rounding bin.
export function recoveryFacts(episode, observation) {
  const goal = structuredClone(episode?.goal || {});
  const world = observation?.system?.contract_version === 1 ? observation.system.world : null;
  const names = [...new Set([goal.holding, ...(goal.on || [])].filter(n => n && n !== 'table'))].sort();
  return {
    goal,
    objects: names.map(name => {
      const obj = observation?.objects?.[name];
      return { name, status: status(obj), center_m: status(obj) === 'visible' ? position(obj.surface_median_xyz) : null,
        support: goal.on?.[1] === name ? world ? (world.facts[`support:${name}`]?.status === 'verified' ? 'available' : 'unknown') : status(observation?.support_evidence?.[name]) : null };
    }),
    empty_gripper: world ? status(world.facts.empty_gripper) : status(observation?.empty_gripper_evidence),
    ...(world ? { runtime_fact_statuses: Object.entries(world.facts).map(([key, fact]) => [key, fact.status]).sort() } : {}),
    hold_alert: episode?.runtime_alert ? { code: episode.runtime_alert.code,
      object_name: episode.runtime_alert.object_name, hold_confirmed: episode.runtime_alert.hold_confirmed === true } : null,
  };
}

export function newRecoveryMemory() { return { version: 2, attempts: [] }; }

function sameFacts(a, b) {
  if (!a || !b || JSON.stringify(a.goal) !== JSON.stringify(b.goal) || a.empty_gripper !== b.empty_gripper
      || JSON.stringify(a.hold_alert) !== JSON.stringify(b.hold_alert) || a.objects.length !== b.objects.length
      || JSON.stringify(a.runtime_fact_statuses) !== JSON.stringify(b.runtime_fact_statuses)) return false;
  return a.objects.every((object, i) => {
    const other = b.objects[i];
    if (object.name !== other.name || object.status !== other.status || object.support !== other.support) return false;
    if (!object.center_m || !other.center_m) return object.center_m === other.center_m;
    return object.center_m.every((v, axis) => Math.abs(v-other.center_m[axis]) <= .01);
  });
}

export function recordRecoveryAttempt(memory, { decision, segment, verification, facts, revision, observation }) {
  const feedback = segment?.feedback;
  memory.attempts.push({ decision: decision.decision, code_key: ['run_code', 'run_skill'].includes(decision.decision) ? actionKey(decision) : null,
    revision, fact_key: digest(facts), evidence_before: facts,
    observation: observation ? { evidence_ref: observation.evidence_ref, captured_at: observation.captured_at,
      control_step: observation.control_step } : null,
    segment_id: segment?.id || null, execution: segment?.execution || null,
    rejected_code: feedback?.category === 'invalid_program' ? decision.code || null : null,
    feedback: feedback ? { category: feedback.category, api: feedback.api, message: feedback.message,
      diagnostic: feedback.category === 'invalid_program' ? feedback.evidence || null : null,
      causal_status: feedback.causal_status || 'not_established' } : null,
    api_attempts: segment?.api_attempts || [], acquisition: segment?.evidence_acquisition || null,
    verification_required: Boolean(verification || segment?.task_binding || segment?.contract?.postcondition || segment?.stage_verification?.condition),
    verification: verification || segment?.node_verification || segment?.stage_verification || null });
  // The episode decision limit is twelve; leave headroom for audit without
  // creating a second, unbounded memory subsystem.
  if (memory.attempts.length > 40) memory.attempts.shift();
}

function blockedAttempt(attempt) {
  if (attempt.feedback || ['failed', 'cancelled'].includes(attempt.execution)) return true;
  // Observing again without a fact change is still an unsuccessful attempt
  // to acquire new evidence. A healthy grasp phase without a declared public
  // postcondition is neither verified holding nor a failed verification.
  if (attempt.decision === 'observe') return true;
  const required = attempt.verification_required ?? Boolean(attempt.verification?.condition || attempt.decision === 'finish');
  return required && ['failed', 'unknown'].includes(attempt.verification?.status);
}

export function recoveryContext(memory, episode, observation) {
  const facts = recoveryFacts(episode, observation), factKey = digest(facts);
  let same = [];
  for (let i = memory.attempts.length - 1; i >= 0; i--) {
    const attempt = memory.attempts[i];
    if (!sameFacts(attempt.evidence_before, facts)) break;
    same.unshift(attempt);
  }
  const missing = facts.objects.filter(o => o.status !== 'visible').map(o => ({ fact: `visible:${o.name}`, status: 'unknown' }));
  for (const object of facts.objects) {
    if (object.support === 'unknown') missing.push({ fact: `support_geometry:${object.name}`, status: 'unknown' });
  }
  const latest = memory.attempts.at(-1);
  const currentInvalid = latest?.feedback?.category === 'invalid_program' ? latest : null;
  let blockedCount = 0;
  for (let i = same.length-1; i >= 0 && blockedAttempt(same[i]); i--) blockedCount++;
  const blocked = missing.length > 0 || blockedCount > 0;
  const protocol = observation?.bounded_recovery_state;
  const routes = [];
  const retreat = observation?.recovery_options?.vertical_retreat;
  if (retreat?.status === 'available') routes.push({ skill: 'vertical_retreat', status: 'available', evidence: retreat });
  if (goalNeedsSupport(episode) && observation?.capabilities?.includes('inspect_support')) {
    const support = episode.goal.on[1], held = episode.goal.on[0];
    const session = protocol?.support_sessions?.find(s => s.support === support && s.held_object === held);
    const exhausted = session && (session.views_used >= 2 || session.blocked);
    // Missing the held object is not permission to move it to an inspection
    // view. The skill itself will still require a fresh verified holding check.
    routes.push({ skill: 'inspect_support', status: exhausted ? 'exhausted' : missing.some(x => x.fact === `visible:${held}`)
      ? 'holding_evidence_required' : protocol ? 'requires_runtime_preconditions' : 'current_budget_unknown',
      views_used: session?.views_used ?? null, support, held_object: held });
  }
  if (goalNeedsSupport(episode) && observation?.capabilities?.includes('inspect_placement') && facts.empty_gripper === 'verified') {
    const session = protocol?.placement_session;
    const exhausted = session && (session.views_used >= 2 || session.blocked);
    // This route's existence does not prove clearance or a recorded release.
    routes.push({ skill: 'inspect_placement', status: exhausted ? 'exhausted' : protocol && !session
      ? 'recorded_release_required' : protocol ? 'requires_runtime_preconditions' : 'current_budget_unknown',
      views_used: session?.views_used ?? null });
  }
  const repeated = blockedCount >= 2;
  const instruction = currentInvalid ? 'Repair the latest rejected Python using the documented API return schema and diagnostic, then re-check the intended phase in the same episode. A program error is not evidence of physical failure or exhausted sensing routes. Do not repeat rejected code or infer that the environment needs a reset. All physical preconditions and cumulative budgets still apply.'
    : repeated ? 'No task-relevant evidence change after repeated blocked attempts. Prefer ask_user if no distinct applicable bounded recovery remains. Do not repeat observation, failed code or finish. New frame/time is not progress.'
      : blocked ? 'Select one applicable recovery tied to the missing fact; read its fresh result before proceeding.' : 'Continue the current verifiable phase.';
  return { version: 2, facts, fact_key: factKey,
    current_evidence: { evidence_ref: observation?.evidence_ref, captured_at: observation?.captured_at,
      control_step: observation?.control_step }, missing_facts: missing, consecutive_unchanged_attempts: same.length,
    consecutive_blocked_attempts: blockedCount,
    repeated_no_progress: repeated, bounded_routes: routes,
    program_repair: currentInvalid ? { status: 'revise_rejected_code', segment_id: currentInvalid.segment_id,
      rejected_code: currentInvalid.rejected_code || null,
      diagnostic: currentInvalid.feedback.diagnostic, scope: 'Code repair only; not a registered robot skill or physical permission' } : null,
    recent_attempts: memory.attempts.slice(-6), recommendation: currentInvalid ? 'revise_code'
      : repeated ? 'request_help_or_distinct_bounded_recovery' : blocked ? 'gather_evidence_or_recover' : 'continue',
    instruction, causal_status: 'not_established',
    scope: 'Public task facts only; a 0.01m per-axis position tolerance is a repetition heuristic, never verification or a physical cause diagnosis.' };
}

const goalNeedsSupport = episode => episode?.goal?.on && episode.goal.on[1] !== 'table';

export function guardRecoveryDecision(memory, context, decision) {
  if (decision.decision === 'ask_user') return null;
  const unchanged = context.consecutive_unchanged_attempts ? memory.attempts.slice(-context.consecutive_unchanged_attempts) : [];
  const invalid = memory.attempts.filter(a => a.feedback?.category === 'invalid_program');
  if (['run_code', 'run_skill'].includes(decision.decision) && [...unchanged, ...invalid].some(a => a.code_key === actionKey(decision) &&
      (a.execution === 'failed' || a.feedback))) {
    return { code: 'REPEATED_FAILED_PROGRAM', message: '相同公开证据下重复提交已失败代码；需要修正或用户协助。' };
  }
  if (context.repeated_no_progress && ['observe', 'finish'].includes(decision.decision)) {
    return { code: 'REPEATED_NO_EVIDENCE_PROGRESS', message: '多次尝试没有改变所需公开证据；请协助恢复观测或选择其他适用恢复方法。' };
  }
  return null;
}
