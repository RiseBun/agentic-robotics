// Wait for sensor readiness, never for a task fact to become true.
import { setTimeout as delay } from 'node:timers/promises';
import { boundedOperation } from './deadline.mjs';

export async function acquireDecisionEvidence({ read, signal, assertCurrent, record, milliseconds = 3000 }) {
  if (!Number.isFinite(milliseconds) || milliseconds < 0 || milliseconds > 3000) throw new Error('Invalid evidence wait bound');
  const started = performance.now();
  let receipt, polls = 0, announced = false, timedOut = false;
  while (true) {
    assertCurrent();
    const remaining = Math.max(1, milliseconds-(performance.now()-started));
    const sent = performance.now();
    try {
      const observation = await boundedOperation(read,
        { signal, milliseconds: receipt ? remaining : Math.max(1, milliseconds || 3000), label: 'Decision observation' });
      assertCurrent();
      const received = performance.now();
      receipt = { observation, transport_milliseconds: received-sent, received_at: received };
      polls++;
    } catch (error) {
      assertCurrent();
      if (signal?.aborted || error.code !== 'OPERATION_TIMEOUT' || !receipt) throw error;
      timedOut = true;
      break;
    }
    if (receipt.observation.decision_evidence?.status !== 'pending' || milliseconds === 0) break;
    if (!announced) {
      announced = true;
      await record('decision_evidence_wait_started', { evidence: receipt.observation.decision_evidence, timeout_milliseconds: milliseconds });
      assertCurrent();
    }
    const left = milliseconds-(performance.now()-started);
    if (left <= 0) { timedOut = true; break; }
    await delay(Math.min(100, left), undefined, { signal });
    assertCurrent();
    if (performance.now()-started >= milliseconds) { timedOut = true; break; }
  }
  const evidence = receipt.observation.decision_evidence;
  const report = { status: timedOut ? 'timed_out' : evidence?.status || 'unsupported',
    elapsed_milliseconds: performance.now()-started, polls,
    evidence_ref: receipt.observation.evidence_ref, readiness: evidence || null,
    scope: 'Sensor age and temporal coverage only; ready does not establish any task fact.' };
  if (announced) await record('decision_evidence_wait_finished', report);
  assertCurrent();
  return { ...receipt, wait: report };
}

// An assistance proposal based on a blocked cut must not ignore a route that
// became admissible while the model was thinking. Reconsider once from fresh
// evidence; this never authorizes an action or overrides a repeated request.
export function newlyAvailableRoute(before, after) {
  const old = before?.system, current = after?.system;
  if (old?.contract_version !== 1 || current?.contract_version !== 1
      || !old.episode_id || old.episode_id !== current.episode_id
      || old.goal_version !== current.goal_version || current.world?.fresh !== true
      || current.executor?.status !== 'ready' || current.executor?.active_segment
      || current.executor?.hold_pending || current.task?.assistance) return false;
  const prior = old.task?.execution?.ready_nodes;
  const ready = current.task?.execution?.ready_nodes;
  return Array.isArray(prior) && prior.length === 0 && Array.isArray(ready) && ready.length > 0;
}
