import crypto from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { requestDecision } from './model.mjs';
import { boundedOperation } from './deadline.mjs';
import { newRecoveryMemory, recoveryFacts, recoveryContext, recordRecoveryAttempt, guardRecoveryDecision } from './recovery.mjs';

export class RobotCoordinator {
  constructor({ root, configuration, client, model = requestDecision, experience = null }) {
    Object.assign(this, { root, configuration, client, model, experience });
    this.epoch = 0;
    this.state = { status: 'idle', messages: [], events: [], history: [], remaining_steps: [], subgoal: '', episode: null, observation: null, evidence: null, verification: { status: 'unknown' } };
    this.eventCursor = 0;
    this.recoveryEnabled = true;
    this.state.recovery = newRecoveryMemory();
    this.recordQueue = Promise.resolve();
    this.controlQueue = Promise.resolve();
  }
  control(fn) {
    const next = this.controlQueue.then(fn, fn);
    this.controlQueue = next.catch(() => {});
    return next;
  }
  async restore() {
    const pointer = path.join(this.root, 'current.json');
    const saved = await fs.readFile(pointer, 'utf8').catch(error => { if (error.code === 'ENOENT') return null; throw error; });
    if (!saved) return;
    const data = JSON.parse(saved);
    Object.assign(this, { taskId: data.taskId, task: data.task, seed: data.seed, state: data.state,
      taskStartedAt: data.taskStartedAt, preflightDecisions: data.preflightDecisions || 0 });
    this.recordDir = path.join(this.root, this.taskId);
    this.state.recovery ||= newRecoveryMemory();
    this.state.status = 'stopping';
    if (this.state.episode) {
      try {
        // Startup must remain bounded even when the last server is unreachable.
        await this.refresh(AbortSignal.timeout(5000));
        if (!['closed', 'interrupted'].includes(this.state.episode.status)) {
          const held = await this.client.request(`/episodes/${this.state.episode.id}/cancel`, {}, AbortSignal.timeout(5000));
          if (!held.stopped) throw new Error('Runtime 尚未确认保持状态');
        }
        this.state.status = 'stopped';
      } catch (error) {
        this.state.status = 'needs_attention';
        this.state.connection_error = error.message;
      }
      this.connectEvents(this.state.episode.id);
    } else this.state.status = 'stopped';
  }
  async record(kind, values) {
    if (!this.recordDir) return;
    const item = { id: crypto.randomUUID(), task_id: this.taskId, episode_id: this.state.episode?.id || null,
      segment_id: this.state.episode?.active_segment || null, time: new Date().toISOString(), kind, ...values };
    const directory = this.recordDir;
    this.recordQueue = this.recordQueue.then(async () => {
      await fs.mkdir(directory, { recursive: true });
      await fs.appendFile(path.join(directory, 'workbench.jsonl'), JSON.stringify(item) + '\n');
      const checkpoint = { taskId: this.taskId, task: this.task, seed: this.seed, state: this.state,
        taskStartedAt: this.taskStartedAt, preflightDecisions: this.preflightDecisions || 0 };
      const temp = path.join(this.root, 'current.tmp');
      await fs.writeFile(temp, JSON.stringify(checkpoint));
      await fs.rename(temp, path.join(this.root, 'current.json'));
    });
    await this.recordQueue;
  }
  assertCurrent(epoch) {
    if (epoch !== this.epoch || this.abort?.signal.aborted) throw new DOMException('Superseded', 'AbortError');
  }
  async wait(epoch, milliseconds = 250) {
    await delay(milliseconds, undefined, { signal: this.abort.signal });
    this.assertCurrent(epoch);
    if (this.taskStartedAt && Date.now() - this.taskStartedAt >= 300_000) throw new Error('任务墙钟预算已用尽，请结束场景');
  }
  async start({ task, text, images = [], seed = 0 }) {
    if (!['cube_lifting', 'cube_stack', 'cube_restack'].includes(task)) throw new Error('不支持的场景');
    if (this.state.episode && !['closed', 'interrupted'].includes(this.state.episode.status)) throw new Error('请先结束当前场景');
    if (this.state.status === 'interpreting') throw new Error('正在创建任务');
    this.eventsAbort?.abort();
    this.taskId = crypto.randomUUID();
    this.taskStartedAt = Date.now();
    this.recordDir = path.join(this.root, this.taskId);
    this.task = task;
    this.preflightDecisions = 0;
    this.image = this.imageRef = undefined;
    this.seed = seed;
    this.eventCursor = 0;
    this.state = { status: 'idle', messages: [], events: [], history: [], remaining_steps: [], subgoal: '', episode: null, observation: null, evidence: null, verification: { status: 'unknown' } };
    this.state.recovery = newRecoveryMemory();
    return this.input({ text, images });
  }
  async input({ text, images = [] }) {
    if (typeof text !== 'string' || !text.trim() || text.length > 8000) throw new Error('请输入具体目标（最多 8000 字符）');
    if (!Array.isArray(images) || images.length > 4 || images.some(x => typeof x !== 'string' || !/^data:image\/(png|jpeg|webp);base64,[A-Za-z0-9+/=]+$/.test(x) || x.length > 8_000_000)) throw new Error('图片格式或大小不支持');
    if (!this.taskId) throw new Error('请先创建任务');
    if (!images.length && /^(状态|进展|现在进展|当前状态|status)[？?。.!！\s]*$/i.test(text.trim())) {
      await this.refresh();
      this.state.messages.push({ role: 'user', text }, { role: 'system', text: `当前状态：${this.state.status}；执行结果以右侧 Runtime 记录为准。` });
      await this.record('status_query', {});
      return { accepted: true, task_id: this.taskId };
    }
    // Fence locally first: a late model reply can never race the stop request.
    const epoch = ++this.epoch;
    this.abort?.abort();
    this.abort = new AbortController();
    this.state.status = 'stopping';
    if (this.state.episode) {
      const result = await this.control(() => this.client.request(`/episodes/${this.state.episode.id}/cancel`, {}));
      this.assertCurrent(epoch);
      if (!result.stopped) throw new Error('Runtime 尚未确认保持状态');
      await this.waitForSegment(epoch);
    }
    this.assertCurrent(epoch);
    this.text = text;
    this.state.recovery = newRecoveryMemory();
    delete this.state.assistance;
    delete this.state.stop_confirmed;
    this.state.evidence = null;
    this.images = images;
    this.state.messages.push({ role: 'user', text, images });
    await this.record('user_input', { text, images, epoch });
    this.state.status = 'interpreting';
    this.launchLoop(epoch);
    return { accepted: true, task_id: this.taskId };
  }
  launchLoop(epoch, run = () => this.run(epoch)) {
    const wallRemaining = Math.max(1, 300_000 - (Date.now() - this.taskStartedAt));
    this.loopPromise = boundedOperation(signal => {
      this.deadlineSignal = signal;
      return run();
    }, { signal: this.abort.signal, milliseconds: wallRemaining, label: 'Task wall budget' }).catch(async error => {
      if (epoch !== this.epoch) return;
      // Fence even providers that keep running after the deadline. Protective
      // cancellation remains allowed after ordinary task dispatch is fenced.
      ++this.epoch;
      this.abort.abort(error);
      this.state.status = 'needs_attention';
      this.state.messages.push({ role: 'system', text: error.message });
      await this.record('agent_error', { error: error.message });
      if (this.state.episode) {
        try {
          const stopped = await boundedOperation(signal => this.client.request(`/episodes/${this.state.episode.id}/cancel`, {}, signal),
            { milliseconds: 5000, label: 'Runtime protective hold' });
          this.state.stop_confirmed = stopped.stopped === true;
          await this.record('protective_hold', { result: stopped });
        } catch (stopError) {
          this.state.stop_confirmed = false;
          this.state.connection_error = stopError.message;
          await this.record('protective_hold_unconfirmed', { error: stopError.message });
        }
      }
    });
    return this.loopPromise;
  }
  async decide(epoch, mode) {
    this.assertCurrent(epoch);
    const remainingWall = 300_000 - (Date.now() - (this.taskStartedAt || Date.now()));
    if (remainingWall <= 0) throw new Error('任务墙钟预算已用尽，请结束场景');
    // Count all model attempts, including invalid outputs and interpretation.
    if (this.state.episode) await this.reserveDecision(epoch, crypto.randomUUID());
    else if ((this.preflightDecisions = (this.preflightDecisions || 0) + 1) > 12) throw new Error('任务模型决策预算已用尽');
    const configuration = await boundedOperation(() => this.configuration(),
      { signal: this.deadlineSignal || this.abort.signal, milliseconds: Math.min(15_000, remainingWall), label: 'Model configuration' });
    const observation = this.state.observation;
    const receipt = this.observationReceipt?.observation === observation ? this.observationReceipt : null;
    const context = { mode, scenario: this.task, user_goal: this.text, goal: this.state.episode?.goal,
      revision: this.state.episode?.revision, latest_observation: observation,
      uncertainty: observation ? 'Missing/occluded geometry is unknown; image time may lag current simulation' : 'No scene created yet',
      current_subgoal: this.state.subgoal, remaining_steps: this.state.remaining_steps,
      // Events stay in the persisted/exportable history. Re-sending the audit
      // stream duplicates whole observations and verification windows inside
      // every segment; decisions need the segment's code and authoritative result.
      recent_attempts: this.state.history.slice(-6).map(({ events, ...segment }) => segment), used_budget: this.state.episode?.used,
      latest_evidence: this.state.evidence ? { ...this.state.evidence,
        age_seconds: Number.isFinite(this.state.evidence.captured_at) ? Date.now()/1000 - this.state.evidence.captured_at : null,
        usage: 'Historical acquisition result. Current facts must come from latest_observation.' } : null,
      remaining_wall_milliseconds: remainingWall,
      limits: this.state.episode?.limits || { decisions: 12, calls: 40, motion_steps: 1500, wall_seconds: 300 } };
    if (mode === 'execution' && this.recoveryEnabled) {
      context.recovery_progress = recoveryContext(this.state.recovery, this.state.episode, observation);
    }
    if (mode === 'execution' && this.experience && this.state.episode) {
      const started = Date.now();
      try {
        const query = { observation, episode: this.state.episode,
          provenance: receipt?.provenance,
          observation_age_upper_seconds: this.observationAge(receipt) };
        const result = await boundedOperation(() => this.experience.retrieve(query),
        { signal: this.deadlineSignal || this.abort.signal, milliseconds: 2000, label: 'Experience retrieval' });
        this.assertCurrent(epoch);
        context.retrieved_experiences = result.matches;
        context.experience_usage = result.usage;
        await this.record('experience_retrieval', { epoch, revision: context.revision,
          corpus_sha256: result.corpus_sha256, matches: result.matches.map(m => m.id),
          evidence_ref: observation?.evidence_ref, observation_age_upper_seconds: query.observation_age_upper_seconds,
          rejected: result.rejected, elapsed_milliseconds: Date.now()-started });
      } catch (error) {
        this.assertCurrent(epoch);
        await this.record('experience_unavailable', { epoch, error: error.message, elapsed_milliseconds: Date.now()-started });
      }
    }
    const images = [...this.images];
    context.scene_image_sources = [];
    if (observation?.image_ref) {
      const bytes = await this.client.bytes(`/episodes/${this.state.episode.id}/artifact?name=${encodeURIComponent(observation.image_ref)}`);
      context.scene_image_sources.push({ input_index: images.length, camera_name: 'robot0_robotview',
        image_ref: observation.image_ref, evidence_ref: observation.evidence_ref, captured_at: observation.captured_at });
      images.push('data:image/png;base64,' + bytes.toString('base64'));
    }
    const wrist = observation?.camera_views?.robot0_eye_in_hand;
    if (wrist?.camera_name === 'robot0_eye_in_hand' && wrist.episode_id === this.state.episode?.id
        && wrist.control_step === observation.control_step && Number.isFinite(wrist.captured_at)
        && Math.abs(wrist.captured_at-observation.captured_at) <= .15
        && /^view-robot0_eye_in_hand-\d{6,}\.png$/.test(wrist.image_ref)) {
      const bytes = await this.client.bytes(`/episodes/${this.state.episode.id}/artifact?name=${encodeURIComponent(wrist.image_ref)}`);
      context.scene_image_sources.push({ input_index: images.length, camera_name: wrist.camera_name,
        image_ref: wrist.image_ref, evidence_ref: wrist.evidence_ref, captured_at: wrist.captured_at });
      images.push('data:image/png;base64,' + bytes.toString('base64'));
    }
    context.latest_observation_age_upper_seconds = this.observationAge(receipt);
    this.assertCurrent(epoch);
    this.state.status = 'thinking';
    const pending = { episode_id: this.state.episode?.id, revision: context.revision, controller: new AbortController() };
    this.pendingModel = pending;
    let decision;
    try {
      decision = await boundedOperation(signal => this.model(configuration.active, context, images, signal,
        (kind, values) => epoch === this.epoch ? this.record(kind, { ...values, epoch, revision: context.revision,
          ...(pending.controller.signal.aborted ? { discarded: true } : {}) }) : undefined),
        { signal: AbortSignal.any([this.deadlineSignal || this.abort.signal, pending.controller.signal]),
          milliseconds: Math.min(90_000, Math.max(1, 300_000 - (Date.now()-this.taskStartedAt))), label: 'Model decision' });
    } finally { if (this.pendingModel === pending) this.pendingModel = null; }
    this.assertCurrent(epoch);
    this.state.messages.push({ role: 'assistant', text: decision.message });
    this.state.subgoal = decision.subgoal || '';
    this.state.remaining_steps = decision.remaining_steps;
    await this.record('decision', { decision, epoch, revision: context.revision });
    this.assertCurrent(epoch);
    return { ...decision, dependency_revision: context.revision };
  }
  observationAge(receipt) {
    // Runtime wall clocks and desktop wall clocks need not agree. Bound age by
    // server-reported age + entire request RTT + local monotonic time since receipt.
    const age = receipt?.observation.age_seconds;
    return Number.isFinite(age) && age >= 0
      ? age + (receipt.transport_milliseconds + performance.now()-receipt.received_at)/1000 : null;
  }
  async acquireDecisionObservation(epoch) {
    let provenance = null;
    if (this.experience) {
      try {
        provenance = await boundedOperation(signal => this.client.request('/health', undefined, signal),
          { signal: this.deadlineSignal || this.abort.signal, milliseconds: 2000, label: 'Experience provenance' });
      } catch (error) {
        this.assertCurrent(epoch);
        await this.record('experience_provenance_unavailable', { error: error.message });
      }
    }
    this.assertCurrent(epoch);
    const started = performance.now();
    const observation = await this.client.request(`/episodes/${this.state.episode.id}/observe`);
    this.assertCurrent(epoch);
    const received = performance.now();
    this.state.observation = observation;
    this.observationReceipt = { observation, provenance, transport_milliseconds: received-started, received_at: received };
  }
  async reserveDecision(epoch, requestId) {
    for (let attempt = 0; attempt < 2; attempt++) {
      this.assertCurrent(epoch);
      try {
        return await this.client.request(`/episodes/${this.state.episode.id}/decisions`, {
          request_id: requestId, revision: this.state.episode.revision
        });
      } catch (error) {
        if (error.code !== 'STALE_REVISION' || attempt === 1) throw error;
        // No model request or physical action has happened. Preserve the same
        // idempotency key and refresh evidence before reserving its budget.
        await this.refresh();
        this.assertCurrent(epoch);
        await this.acquireDecisionObservation(epoch);
        this.assertCurrent(epoch);
        await this.record('decision_revision_refreshed', { request_id: requestId, revision: this.state.episode.revision });
      }
    }
  }
  async run(epoch) {
    let decision = await this.decide(epoch, 'interpretation');
    if (decision.decision === 'ask_user') { this.state.status = 'awaiting_user'; return; }
    if (decision.decision !== 'observe' || !decision.goal) throw new Error('需要先明确受支持的目标关系');
    if (this.state.episode) {
      this.state.episode = await this.control(() => { this.assertCurrent(epoch); return this.client.request(`/episodes/${this.state.episode.id}/goal`, { goal: decision.goal }); });
    } else {
      this.state.episode = await this.control(async () => {
        this.assertCurrent(epoch);
        const remainingSeconds = (300_000 - (Date.now() - this.taskStartedAt))/1000;
        if (remainingSeconds <= 0) throw new Error('任务墙钟预算已用尽');
        const episode = await this.client.request('/episodes', { request_id: this.taskId, task: this.task, seed: this.seed, goal: decision.goal,
          limits: { wall_seconds: Math.min(300, remainingSeconds) } });
        this.state.episode = episode;
        if (epoch !== this.epoch) await this.client.request(`/episodes/${episode.id}/cancel`, {});
        return episode;
      });
      this.assertCurrent(epoch);
      // Transfer pre-scene model attempts into the same cumulative decision ledger.
      this.state.status = 'initializing';
      while (this.state.episode.status === 'initializing') {
        await this.wait(epoch);
        await this.refresh();
      }
      for (let i = 0; i < this.preflightDecisions; i++) await this.reserveDecision(epoch, `interpretation-${i}`);
      this.preflightDecisions = 0;
      this.connectEvents(this.state.episode.id);
    }
    this.assertCurrent(epoch);
    return this.runExecution(epoch);
  }
  async runExecution(epoch) {
    this.assertCurrent(epoch);
    let decision;
    while (true) {
      try {
      await this.refresh();
      this.assertCurrent(epoch);
      if (this.state.episode.status !== 'ready') throw new Error(`场景不可继续：${this.state.episode.status}`);
      await this.acquireDecisionObservation(epoch);
      this.assertCurrent(epoch);
      decision = await this.decide(epoch, 'execution');
      await this.refresh();
      this.assertCurrent(epoch);
      if (decision.dependency_revision !== this.state.episode.revision) {
        await this.record('stale_decision_discarded', { expected_revision: decision.dependency_revision, current_revision: this.state.episode.revision });
        continue;
      }
      const facts = recoveryFacts(this.state.episode, this.state.observation);
      const progress = this.recoveryEnabled ? recoveryContext(this.state.recovery, this.state.episode, this.state.observation) : null;
      if (decision.decision === 'ask_user') { await this.requestAssistance(epoch, 'model', decision.message); return; }
      if (decision.decision === 'finish') {
        if (await this.verifyGoal(epoch, decision.dependency_revision, 'model_finish')) return;
        this.rememberRecovery(decision, facts, null, this.state.verification);
        const block = progress && guardRecoveryDecision(this.state.recovery, progress, decision);
        if (block) { await this.requestAssistance(epoch, 'recovery_guard', block.message, block); return; }
        continue;
      }
      const block = progress && guardRecoveryDecision(this.state.recovery, progress, decision);
      if (block) { await this.requestAssistance(epoch, 'recovery_guard', block.message, block); return; }
      if (decision.decision === 'observe') {
        this.rememberRecovery(decision, facts);
        await this.record('recovery_progress', { decision: 'observe', progress });
        await this.wait(epoch, 1000); continue;
      }
      this.assertCurrent(epoch);
      const result = await this.control(() => { this.assertCurrent(epoch); return this.client.request(`/episodes/${this.state.episode.id}/segments`, {
        request_id: crypto.randomUUID(), revision: decision.dependency_revision, subgoal: decision.subgoal || '', code: decision.code,
        ...(decision.contract !== undefined ? { contract: decision.contract } : {})
      }); });
      // If input arrived in-flight, cancellation must include this just-admitted segment.
      if (epoch !== this.epoch) return;
      this.state.status = 'executing';
      this.state.stop_confirmed = false;
      await this.waitForSegment(epoch);
      this.assertCurrent(epoch);
      const segment = this.state.episode.segments[result.segment_id];
      this.rememberRecovery(decision, facts, segment);
      this.state.history.push({ ...segment, events: this.state.events.filter(x => x.segment_id === result.segment_id) });
      if (segment?.evidence_acquisition) {
        this.state.evidence = segment.evidence_acquisition;
        await this.record('evidence_feedback', { latest: this.state.evidence });
      }
      const evidenceEvents = this.state.events.filter(x => x.segment_id === result.segment_id &&
        ['evidence_acquisition_started', 'view_attempt', 'view_result', 'evidence_acquisition_finished'].includes(x.kind));
      if (evidenceEvents.length) {
        // Segment state is authoritative. SSE may still only have delivered
        // the start event when execution finishes; it must not replace the
        // final acquisition with incomplete or delayed feedback.
        if (!segment?.evidence_acquisition) {
          this.state.evidence = evidenceEvents.findLast(x => x.kind === 'evidence_acquisition_finished') || null;
        }
        await this.record('evidence_feedback', { events: evidenceEvents, latest: this.state.evidence });
      }
      this.state.verification = segment?.goal_verification || { status: segment?.effect || 'unknown' };
      await this.record('segment_feedback', { segment });
      if (this.recoveryEnabled) await this.record('recovery_progress', {
        progress: recoveryContext(this.state.recovery, this.state.episode, this.state.observation), segment_id: segment?.id });
      // The final relation is already part of the registered user goal. A
      // successful Runtime check does not need another model to propose finish.
      // Recheck it at the current revision before accepting completion.
      if (segment?.execution === 'completed' && segment.goal_verification?.status === 'verified') {
        if (await this.verifyGoal(epoch, this.state.episode.revision, 'runtime_segment_goal')) return;
      }
      } catch (error) {
        if (error.code === 'HOLD_PENDING') {
          this.assertCurrent(epoch);
          await this.record('monitor_hold_pending', { reason: error.message });
          await this.wait(epoch, 50);
          continue;
        }
        if (error.code !== 'STALE_REVISION') throw error;
        this.assertCurrent(epoch);
        await this.record('stale_decision_discarded', { reason: error.message });
        // No action was admitted. Refresh evidence and re-decide within the
        // original cumulative budget rather than terminating the task.
      }
    }
  }
  rememberRecovery(decision, facts, segment = null, verification = null) {
    if (this.recoveryEnabled) recordRecoveryAttempt(this.state.recovery, {
      decision, facts, segment, verification, observation: this.state.observation, revision: decision.dependency_revision });
  }
  async requestAssistance(epoch, origin, message, block = null) {
    this.assertCurrent(epoch);
    const result = await this.control(() => {
      this.assertCurrent(epoch);
      return this.client.request(`/episodes/${this.state.episode.id}/cancel`, {});
    });
    this.assertCurrent(epoch);
    if (!result.stopped) throw new Error('请求协助前 Runtime 尚未确认保持状态');
    this.state.stop_confirmed = true;
    this.state.assistance = { origin, message, block, hold_confirmed: true };
    if (origin !== 'model') this.state.messages.push({ role: 'system', text: message });
    this.state.status = 'awaiting_user';
    await this.record('assistance_requested', this.state.assistance);
    this.assertCurrent(epoch);
  }
  async verifyGoal(epoch, revision, source) {
    this.assertCurrent(epoch);
    const id = this.state.episode.id;
    const goal = structuredClone(this.state.episode.goal);
    const goalKey = value => JSON.stringify([value?.holding ?? null, value?.on ?? null, value?.empty_gripper ?? null]);
    let result;
    // Verification is a read of the registered goal, not a new physical action.
    // A scene revision can change after a segment ends and before this request
    // arrives. Refresh and recheck once without consuming another model decision;
    // never carry a completion proposal across a different goal or user epoch.
    for (let attempt = 0; attempt < 2; attempt++) {
      this.assertCurrent(epoch);
      try {
        result = await this.client.request(`/episodes/${id}/verify`, { revision });
        break;
      } catch (error) {
        if (error.code !== 'STALE_REVISION') throw error;
        await this.refresh();
        this.assertCurrent(epoch);
        if (this.state.episode.id !== id || goalKey(this.state.episode.goal) !== goalKey(goal)) {
          throw new Error('验收期间任务目标已改变，请确认当前目标');
        }
        const previous = revision;
        revision = this.state.episode.revision;
        await this.record('verification_revision_refreshed', { source, previous_revision: previous, revision, attempt: attempt + 1 });
        if (attempt === 1) result = { status: 'unknown', reason: 'Scene kept changing during bounded goal verification' };
      }
    }
    this.assertCurrent(epoch);
    this.state.verification = result;
    if (result.status === 'verified') this.state.status = 'completed';
    this.state.history.push({ finish_request: true, verification: result, source });
    await this.record('finish_verification', { result, source });
    this.assertCurrent(epoch);
    return result.status === 'verified';
  }
  async waitForSegment(epoch) {
    do { await this.refresh(); if (!this.state.episode.active_segment) return; await this.wait(epoch); } while (true);
  }
  async refresh(signal) {
    const id = this.state.episode?.id;
    if (!id) return;
    const episode = await this.client.request(`/episodes/${id}`, undefined, signal);
    if (this.state.episode?.id === id) {
      this.state.episode = episode;
      if (episode.status === 'closed' && episode.termination_reason === 'wall_budget_exhausted'
          && !['closed', 'completed', 'needs_attention'].includes(this.state.status)) {
        // Runtime owns the physical deadline, including while the user or a
        // non-cooperating model is silent. Fence its late reply on reconnect.
        ++this.epoch;
        this.abort?.abort();
        this.state.status = 'needs_attention';
        this.state.messages.push({ role: 'system', text: '任务墙钟预算已耗尽；Runtime 已确认保持并结束场景。' });
        await this.record('runtime_task_expired', { episode_id: id, used: episode.used, termination_reason: episode.termination_reason });
      }
    }
    return episode;
  }
  connectEvents(id) {
    this.eventsAbort?.abort();
    this.eventsAbort = new AbortController();
    const signal = this.eventsAbort.signal;
    (async () => {
      while (!signal.aborted && this.state.episode?.id === id && !['closed', 'interrupted'].includes(this.state.episode.status)) {
        try {
          await this.client.events(id, this.eventCursor, signal, async event => {
            if (event.id <= this.eventCursor) return;
            this.eventCursor = event.id;
            this.state.events.push(event);
            const pending = this.pendingModel;
            if (event.kind === 'public_holding_contradicted' && pending?.episode_id === id
                && event.episode_id === id && Number.isInteger(event.revision) && event.revision > pending.revision) {
              const stale = Object.assign(new Error('Public holding monitor invalidated the pending model decision'), { code: 'STALE_REVISION' });
              pending.controller.abort(stale);
            }
            await this.record('runtime_event', { event });
          });
        } catch (error) {
          if (signal.aborted) return;
          this.state.connection_error = error.message;
        }
        await delay(1000, undefined, { signal }).catch(() => {});
      }
    })();
  }
  async stop() {
    ++this.epoch;
    this.abort?.abort();
    this.state.status = 'stopping';
    if (this.state.episode) {
      const stopped = await this.control(() => this.client.request(`/episodes/${this.state.episode.id}/cancel`, {}));
      if (!stopped.stopped) throw new Error('Runtime 尚未确认保持状态');
      await this.refresh();
    }
    this.state.status = 'stopped';
    await this.record('user_stop', {});
    return this.state;
  }
  async close() {
    if (this.state.episode && ['failed', 'interrupted', 'closed'].includes(this.state.episode.status)) {
      ++this.epoch;
      this.abort?.abort();
    } else await this.stop();
    if (this.state.episode) {
      if (this.state.episode.status !== 'interrupted') this.state.episode = await this.client.request(`/episodes/${this.state.episode.id}/close`, {});
      else this.state.episode.status = 'closed';
      await this.record('episode_closed', {});
      if (this.experience && this.state.episode.termination_reason !== 'interrupted') {
        try {
          const candidate = await boundedOperation(async signal => {
            const trace = await this.client.request(`/episodes/${this.state.episode.id}/trace`, undefined, signal);
            return this.experience.propose(trace);
          }, { milliseconds: 5000, label: 'Closed-task experience extraction' });
          await this.record('experience_candidate', { id: candidate?.id || null, validated: false });
        } catch (error) {
          await this.record('experience_extraction_unavailable', { error: error.message });
        }
      }
    }
    this.eventsAbort?.abort();
    this.state.status = 'closed';
    return this.state;
  }
  async view() {
    if (this.state.episode) {
      try {
        await this.refresh();
        if (['closed', 'interrupted'].includes(this.state.episode.status)) {
          delete this.state.connection_error;
          return { ...this.state, task_id: this.taskId, image: this.image, events: this.state.events.slice(-200) };
        }
        this.state.observation = await this.client.request(`/episodes/${this.state.episode.id}/observe`);
        const ref = this.state.observation.image_ref;
        if (ref && this.imageRef !== ref) {
          const bytes = await this.client.bytes(`/episodes/${this.state.episode.id}/artifact?name=${encodeURIComponent(ref)}`);
          this.image = 'data:image/png;base64,' + bytes.toString('base64');
          this.imageRef = ref;
        }
        delete this.state.connection_error;
      } catch (error) { this.state.connection_error = error.message; }
    }
    return { ...this.state, task_id: this.taskId, image: this.image, events: this.state.events.slice(-200) };
  }
  async export() {
    await this.recordQueue;
    const workbench = this.recordDir ? await fs.readFile(path.join(this.recordDir, 'workbench.jsonl'), 'utf8') : '';
    const runtime = this.state.episode ? await this.client.bytes(`/episodes/${this.state.episode.id}/export`, 600_000) : null;
    return { version: 1, task_id: this.taskId, episode_id: this.state.episode?.id, state: this.state, workbench_jsonl: workbench,
      runtime_zip_base64: runtime?.toString('base64'), exported_at: new Date().toISOString() };
  }
}
