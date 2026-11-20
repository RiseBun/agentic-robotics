import test from 'node:test';
import assert from 'node:assert/strict';
import { newRecoveryMemory, recoveryFacts, recoveryContext, recordRecoveryAttempt, guardRecoveryDecision } from './recovery.mjs';

const episode = { goal: { on: ['red cube', 'green cube'], empty_gripper: true }, revision: 1 };
const scene = () => ({ evidence_ref: 'frame-1', captured_at: 1, control_step: 1,
  objects: { 'red cube': { status: 'unknown' }, 'green cube': { status: 'visible', surface_median_xyz: [.5, 0, .03] } },
  support_evidence: { 'green cube': { status: 'available' } }, empty_gripper_evidence: { status: 'verified' },
  bounded_recovery_state: { version: 1, support_sessions: [], placement_session: { release_id: 'release', views_used: 0, blocked: false } },
  capabilities: ['inspect_support', 'inspect_placement'] });
function remember(memory, observation, decision = { decision: 'observe' }, segment, e = episode) {
  recordRecoveryAttempt(memory, { decision, segment, facts: recoveryFacts(e, observation), revision: e.revision });
}

test('fresh frames, timestamps, text and subpixel geometry do not manufacture task progress', () => {
  const first = scene(), next = scene();
  Object.assign(next, { evidence_ref: 'frame-99', captured_at: 100, control_step: 100 });
  next.objects['green cube'].surface_median_xyz[0] += .0001;
  const memory = newRecoveryMemory();
  remember(memory, first); remember(memory, next);
  const context = recoveryContext(memory, episode, next);
  assert.equal(context.consecutive_unchanged_attempts, 2);
  assert.equal(context.repeated_no_progress, true);
  assert.equal(context.causal_status, 'not_established');
  assert.deepEqual(context.missing_facts, [{ fact: 'visible:red cube', status: 'unknown' }]);
});

test('recovered public visibility clears unchanged streak and permits a new phase', () => {
  const memory = newRecoveryMemory(), before = scene(), after = scene();
  remember(memory, before); remember(memory, before);
  after.objects['red cube'] = { status: 'visible', surface_median_xyz: [.5, .1, .04] };
  const context = recoveryContext(memory, episode, after);
  assert.equal(context.repeated_no_progress, false);
  assert.equal(context.consecutive_unchanged_attempts, 0);
  assert.equal(guardRecoveryDecision(memory, context, { decision: 'run_code', code: 'close_gripper()' }), null);
});

test('position noise crossing a centimetre rounding boundary still is not evidence progress', () => {
  const memory = newRecoveryMemory(), before = scene(), after = scene();
  before.objects['green cube'].surface_median_xyz[0] = .50499;
  after.objects['green cube'].surface_median_xyz[0] = .50501;
  remember(memory, before); remember(memory, before);
  assert.equal(recoveryContext(memory, episode, after).repeated_no_progress, true);
  after.objects['green cube'].surface_median_xyz[0] = .52501;
  assert.equal(recoveryContext(memory, episode, after).consecutive_unchanged_attempts, 0);
});

test('unchanged invalid code is blocked; a corrected name is allowed', () => {
  const memory = newRecoveryMemory(), observation = scene();
  const decision = { decision: 'run_code', code: 'open_gripper()\np=nz' };
  remember(memory, observation, decision, { id: 'bad', execution: 'failed', api_attempts: [],
    feedback: { category: 'invalid_program', message: "Unbound local name 'nz'" } });
  const context = recoveryContext(memory, episode, observation);
  assert.equal(guardRecoveryDecision(memory, context, decision).code, 'REPEATED_FAILED_PROGRAM');
  assert.equal(guardRecoveryDecision(memory, context, { ...decision, code: 'z=0.1\np=z' }), null);
  assert.equal(context.recent_attempts[0].api_attempts.length, 0);
});

test('two exhausted views stay recorded while a distinct available retreat is preserved', () => {
  const memory = newRecoveryMemory(), observation = scene();
  observation.objects['red cube'] = { status: 'visible', surface_median_xyz: [.5, 0, .2] };
  observation.support_evidence['green cube'].status = 'unknown';
  observation.recovery_options = { vertical_retreat: { status: 'available', target_xyz: [.5, 0, .3] } };
  observation.bounded_recovery_state.support_sessions = [{ support: 'green cube', held_object: 'red cube', views_used: 2, blocked: false }];
  remember(memory, observation, { decision: 'run_code', code: 'inspect_support("green cube","red cube")' },
    { execution: 'failed', feedback: { category: 'insufficient_evidence' }, evidence_acquisition:
      { support: 'green cube', held_object: 'red cube', status: 'unknown', total_views_used: 2, reason: 'views_exhausted' } });
  remember(memory, observation);
  const context = recoveryContext(memory, episode, observation);
  assert.equal(context.bounded_routes.find(r => r.skill === 'inspect_support').status, 'exhausted');
  assert.equal(context.bounded_routes.find(r => r.skill === 'vertical_retreat').status, 'available');
  assert.equal(guardRecoveryDecision(memory, context, { decision: 'run_code', code: 'goto_pose([0.5,0,0.3],[0,0,1,0])' }), null);
});

test('missing held identity is not inspection permission and a route is not proof of prerequisites', () => {
  const context = recoveryContext(newRecoveryMemory(), episode, scene());
  assert.equal(context.bounded_routes.find(r => r.skill === 'inspect_support').status, 'holding_evidence_required');
  assert.equal(context.bounded_routes.find(r => r.skill === 'inspect_placement').status, 'requires_runtime_preconditions');
});

test('old acquisition results cannot exhaust the current new-grasp session', () => {
  const memory = newRecoveryMemory(), observation = scene();
  observation.objects['red cube'] = { status: 'visible', surface_median_xyz: [.5, 0, .2] };
  remember(memory, observation, { decision: 'run_code', code: 'inspect_support("green cube","red cube")' },
    { execution: 'failed', evidence_acquisition: { support: 'green cube', held_object: 'red cube', total_views_used: 2, reason: 'views_exhausted' } });
  let context = recoveryContext(memory, episode, observation);
  assert.equal(context.bounded_routes.find(r => r.skill === 'inspect_support').status, 'requires_runtime_preconditions');
  delete observation.bounded_recovery_state;
  context = recoveryContext(memory, episode, observation);
  assert.equal(context.bounded_routes.find(r => r.skill === 'inspect_support').status, 'current_budget_unknown');
});

test('repeat guard preserves ask_user and distinguishes a system guard from model decisions', () => {
  const memory = newRecoveryMemory(), observation = scene();
  remember(memory, observation); remember(memory, observation);
  const context = recoveryContext(memory, episode, observation);
  assert.equal(guardRecoveryDecision(memory, context, { decision: 'observe' }).code, 'REPEATED_NO_EVIDENCE_PROGRESS');
  assert.equal(guardRecoveryDecision(memory, context, { decision: 'finish' }).code, 'REPEATED_NO_EVIDENCE_PROGRESS');
  assert.equal(guardRecoveryDecision(memory, context, { decision: 'ask_user' }), null);
});

test('a real fact transition clears prior failed-action restriction; invalid syntax remains invalid', () => {
  const memory = newRecoveryMemory(), before = scene(), changed = scene();
  const decision = { decision: 'run_code', code: 'get_object_pose("red cube")' };
  remember(memory, before, decision, { execution: 'failed', feedback: { category: 'insufficient_evidence' } });
  changed.objects['red cube'] = { status: 'visible', surface_median_xyz: [.5, 0, .05] };
  remember(memory, changed);
  const context = recoveryContext(memory, episode, changed);
  assert.equal(guardRecoveryDecision(memory, context, decision), null);
});

test('healthy grasp without a declared postcondition is not a failed attempt before one code error', () => {
  const memory = newRecoveryMemory(), observation = scene();
  remember(memory, observation, { decision: 'run_code', code: 'close_gripper()' },
    { id: 'grasp', execution: 'completed', stage_verification: { status: 'unknown', reason: 'No public postcondition declared' } });
  let context = recoveryContext(memory, episode, observation);
  assert.equal(context.consecutive_blocked_attempts, 0);
  const invalid = { decision: 'run_code', code: 'state=get_execution_state()\np=state[0]' };
  remember(memory, observation, invalid, { id: 'bad', execution: 'failed',
    feedback: { category: 'invalid_program', message: 'Dictionary has no valid key 0',
      evidence: { kind: 'invalid_subscript', line: 2, available_fields: ['tcp_xyz', 'tcp_wxyz'] } } });
  context = recoveryContext(memory, episode, observation);
  assert.equal(context.consecutive_unchanged_attempts, 2);
  assert.equal(context.consecutive_blocked_attempts, 1);
  assert.equal(context.repeated_no_progress, false);
  assert.equal(context.recommendation, 'revise_code');
  assert.equal(context.program_repair.segment_id, 'bad');
  assert.equal(context.program_repair.rejected_code, invalid.code);
  assert.deepEqual(context.program_repair.diagnostic.available_fields, ['tcp_xyz', 'tcp_wxyz']);
  assert.equal(guardRecoveryDecision(memory, context, invalid).code, 'REPEATED_FAILED_PROGRAM');
  assert.equal(guardRecoveryDecision(memory, context, { ...invalid, code: 'state=get_execution_state()\np=state["tcp_xyz"]' }), null);
});

test('latest invalid program keeps its repair route after two genuinely blocked attempts', () => {
  const memory = newRecoveryMemory(), observation = scene();
  remember(memory, observation); remember(memory, observation);
  remember(memory, observation, { decision: 'run_code', code: 'state=get_execution_state()\np=state[0]' },
    { id: 'invalid', execution: 'failed', feedback: { category: 'invalid_program' } });
  const context = recoveryContext(memory, episode, observation);
  assert.equal(context.repeated_no_progress, true);
  assert.equal(context.recommendation, 'revise_code');
  assert.equal(guardRecoveryDecision(memory, context, { decision: 'observe' }).code, 'REPEATED_NO_EVIDENCE_PROGRESS');
  assert.equal(guardRecoveryDecision(memory, context, { decision: 'ask_user' }), null);
});

test('corrected code clears the program repair route; a later physical failure remains a failure', () => {
  const memory = newRecoveryMemory(), observation = scene();
  remember(memory, observation, { decision: 'run_code', code: 'p=state[0]' },
    { execution: 'failed', feedback: { category: 'invalid_program' } });
  remember(memory, observation, { decision: 'run_code', code: 'p=get_execution_state()' },
    { execution: 'completed', stage_verification: { status: 'unknown', reason: 'No public postcondition declared' } });
  let context = recoveryContext(memory, episode, observation);
  assert.equal(context.program_repair, null);
  assert.equal(context.consecutive_blocked_attempts, 0);
  remember(memory, observation, { decision: 'run_code', code: 'check_holding("red cube")' },
    { execution: 'failed', feedback: { category: 'insufficient_evidence' } });
  context = recoveryContext(memory, episode, observation);
  assert.equal(context.program_repair, null);
  assert.equal(context.recommendation, 'gather_evidence_or_recover');
  remember(memory, observation, { decision: 'run_code', code: 'check_holding("red cube")' },
    { execution: 'failed', feedback: { category: 'condition_not_satisfied' } });
  context = recoveryContext(memory, episode, observation);
  assert.equal(context.repeated_no_progress, true);
  assert.equal(context.recommendation, 'request_help_or_distinct_bounded_recovery');
});

test('declared but unknown postcondition counts as blocked even when code completed', () => {
  const memory = newRecoveryMemory(), observation = scene();
  for (let i=0; i<2; i++) remember(memory, observation, { decision: 'run_code', code: 'close_gripper()' },
    { execution: 'completed', contract: { postcondition: { holding: 'red cube' } }, stage_verification: { status: 'unknown' } });
  const context = recoveryContext(memory, episode, observation);
  assert.equal(context.consecutive_blocked_attempts, 2);
  assert.equal(context.repeated_no_progress, true);
});
