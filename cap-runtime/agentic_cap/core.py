import copy
import hashlib
import json
import threading
import time
import uuid
import concurrent.futures
from pathlib import Path

from .interpreter import Interpreter
from .feedback import EffectError, feedback

TASKS = {'cube_lifting', 'cube_stack', 'cube_restack'}
DEFAULTS = dict(decisions=12, calls=40, motion_steps=1500, wall_seconds=300)
POST_ACTION_EVIDENCE_TIMEOUT_SECONDS = 3.0


def identifier():
    return uuid.uuid4().hex


def plain(value):
    if hasattr(value, 'tolist'):
        return plain(value.tolist())
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise TypeError('Non-public API value: ' + type(value).__name__)


def validate_goal(goal):
    if not isinstance(goal, dict) or set(goal) - {'holding', 'on', 'empty_gripper'}:
        raise ValueError('Goal supports holding, on and empty_gripper only')
    names = {'red cube', 'green cube'}
    if 'holding' in goal and goal['holding'] not in names:
        raise ValueError('Unknown held object')
    if 'empty_gripper' in goal and not isinstance(goal['empty_gripper'], bool):
        raise ValueError('empty_gripper must be boolean')
    if goal.get('holding') and goal.get('empty_gripper'):
        raise ValueError('Holding and empty gripper conflict')
    if goal.get('empty_gripper') is False and not goal.get('holding'):
        raise ValueError('Specify which supported object must be held')
    if 'on' in goal:
        relation = goal['on']
        if not isinstance(relation, list) or len(relation) != 2 or relation[0] not in names or relation[1] not in names | {'table'} or relation[0] == relation[1]:
            raise ValueError('Invalid placement relation')
    if not goal:
        raise ValueError('Goal cannot be empty')
    return copy.deepcopy(goal)


class Halt(RuntimeError):
    def __init__(self, message, code='CANCELLED', evidence=None):
        self.code = code
        self.evidence = evidence
        super().__init__(message)


class StaleRevision(ValueError):
    code = 'STALE_REVISION'


class HoldPending(ValueError):
    code = 'HOLD_PENDING'


class Episode:
    def __init__(self, root, task, seed, goal, backend_factory, limits=None, episode_id=None, creation=None):
        if task not in TASKS:
            raise ValueError('Unsupported task')
        if not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed < 2**32:
            raise ValueError('Seed must be an unsigned 32-bit integer')
        self.id = episode_id or identifier()
        self.creation = creation
        self.directory = Path(root) / self.id
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.cv = threading.Condition(self.lock)
        self.task, self.seed, self.goal = task, seed, validate_goal(goal)
        self.revision = 1
        self.limits = {**DEFAULTS, **(limits or {})}
        if set(self.limits) != set(DEFAULTS) or any(not isinstance(v, (int, float)) or isinstance(v, bool) or not 0 < v <= DEFAULTS[k] for k, v in self.limits.items()):
            raise ValueError('Limits must be positive and cannot exceed defaults')
        self.used = dict(decisions=0, calls=0, motion_steps=0, idle_steps=0)
        self.created = time.time()
        self.started = time.monotonic()
        self.ended = None
        self.termination_reason = None
        self.expiration_hold_serial = None
        self.status = 'initializing'
        self.events, self.segments, self.requests = [], {}, {}
        self.active = None
        self.snapshot = {}
        self.backend = None
        self.target = None
        self.gripper = 1.0
        self.motion = False
        self.hold_requested = False
        self.hold_serial = self.hold_ack = 0
        self.exit = False
        self.backend_factory = backend_factory
        self.scene_facts = None
        self.runtime_alert = None
        self.monitor_requested_at = None
        self.monitor_error = None
        self.thread = threading.Thread(target=self._loop, name='physics-' + self.id, daemon=True)
        self.event('episode_created', task=task, seed=seed, goal=self.goal)
        self.thread.start()

    def event(self, kind, **data):
        with self.lock:
            segment_id = data.pop('segment_id', self.active)
            item = dict(id=len(self.events) + 1, episode_id=self.id,
                        segment_id=segment_id, time=time.time(), kind=kind, **plain(data))
            self.events.append(item)
            with (self.directory / 'events.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(item, ensure_ascii=False) + '\n')
            self._persist()
            self.cv.notify_all()
            return item

    def _persist(self):
        record = self.state()
        temp = self.directory / 'episode.tmp'
        temp.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
        temp.replace(self.directory / 'episode.json')

    def state(self):
        with self.lock:
            return copy.deepcopy(dict(id=self.id, task=self.task, seed=self.seed, goal=self.goal,
                creation=self.creation,
                revision=self.revision, status=self.status, termination_reason=self.termination_reason, active_segment=self.active,
                runtime_alert=self.runtime_alert,
                used={**self.used, 'wall_seconds': (self.ended if self.ended is not None else time.monotonic()) - self.started},
                limits=self.limits, segments=self.segments, last_event_id=len(self.events)))

    def _loop(self):
        try:
            # Construct MuJoCo/OpenGL on the only thread allowed to touch physics.
            self.backend = self.backend_factory(self)
            self.backend.initialize()
            with self.lock:
                self.target = self.backend.joints()
                self.status = 'ready'
                self.event('episode_ready')
            period = self.backend.period
            deadline = time.monotonic()
            last_capture = 0
            while not self.exit:
                with self.lock:
                    if time.monotonic() - self.started >= self.limits['wall_seconds'] and self.status not in {'budget_exhausted', 'closed'}:
                        self.status = 'budget_exhausted'
                        if self.active:
                            self.segments[self.active]['cancel_requested'] = True
                            self.segments[self.active]['halt_code'] = 'BUDGET_EXHAUSTED'
                        self.hold_serial += 1
                        self.expiration_hold_serial = self.hold_serial
                        self.hold_requested = True
                        self.event('budget_exhausted', budget='wall_seconds')
                    if self.hold_requested:
                        self.target = self.backend.joints()
                        self.motion = False
                        self.hold_requested = False
                        ack = self.hold_serial
                    else:
                        ack = None
                    if self.motion and self.used['motion_steps'] >= self.limits['motion_steps']:
                        self.target = self.backend.joints()
                        self.motion = False
                        if self.active:
                            self.segments[self.active]['cancel_requested'] = True
                            self.segments[self.active]['halt_code'] = 'BUDGET_EXHAUSTED'
                        self.event('budget_exhausted', budget='motion_steps')
                    target, gripper, motion = copy.deepcopy(self.target), self.gripper, self.motion
                self.backend.step(target, gripper)
                with self.lock:
                    self.used['motion_steps' if motion else 'idle_steps'] += 1
                    # Rendering is throttled on the owner, never from API/model threads.
                    if not self.snapshot or time.monotonic() - last_capture >= .2:
                        self.snapshot = self.backend.capture()
                        self.snapshot.setdefault('captured_at', time.time())
                        self.snapshot.update(episode_id=self.id, revision=self.revision)
                        self.snapshot.setdefault('simulation_time', self.backend.sim_time())
                        watcher = getattr(self.backend, 'poll_holding_watchdog', None)
                        if watcher is not None:
                            try:
                                report = watcher(copy.deepcopy(self.snapshot))
                                if not isinstance(report, dict) or report.get('status') not in {'inactive', 'unknown', 'supported', 'violated'}:
                                    raise ValueError('Invalid public holding monitor report')
                                self.monitor_error = None
                                if report.get('status') == 'violated':
                                    self.interrupt_public_hold(report)
                            except Exception as exc:
                                if self.monitor_error != str(exc):
                                    self.monitor_error = str(exc)
                                    self.event('holding_watchdog_unavailable', error=str(exc))
                        if self.runtime_alert is not None:
                            self.snapshot['runtime_alert'] = copy.deepcopy(self.runtime_alert)
                        objects = self.snapshot.get('objects', {})
                        facts = {name: (obj.get('status'), obj.get('surface_median_xyz')) for name, obj in objects.items()}
                        changed = False
                        if self.scene_facts is not None and not self.active:
                            for name in set(facts) | set(self.scene_facts):
                                a, b = self.scene_facts.get(name), facts.get(name)
                                if a is None or b is None or a[0] != b[0]:
                                    changed = True
                                elif a[1] and b[1] and sum((x-y)**2 for x, y in zip(a[1], b[1])) > .02**2:
                                    changed = True
                            if changed:
                                self.revision += 1
                                self.snapshot['revision'] = self.revision
                                self.event('scene_changed', revision=self.revision, objects=objects)
                        if not self.active:
                            # Compare to the last material state, not a drifting per-frame baseline.
                            if self.scene_facts is None or changed:
                                self.scene_facts = copy.deepcopy(facts)
                        last_capture = time.monotonic()
                    if ack is not None:
                        self.hold_ack = ack
                        self.event('hold_confirmed', hold_serial=ack)
                        alert = self.runtime_alert
                        if alert is not None and not alert['hold_confirmed'] and ack >= alert['hold_serial']:
                            alert['hold_confirmed'] = True
                            alert['hold_latency_seconds'] = time.monotonic()-self.monitor_requested_at
                            self.event('public_holding_hold_confirmed', monitor_event_id=alert['event_id'],
                                hold_serial=ack, latency_seconds=alert['hold_latency_seconds'])
                    self.cv.notify_all()
                    # A disconnected client cannot keep an exhausted scene
                    # alive indefinitely. The owner first confirms hold and
                    # lets the cancellable executor finish its record.
                    if (self.status == 'budget_exhausted' and self.active is None
                            and self.expiration_hold_serial is not None
                            and self.hold_ack >= self.expiration_hold_serial):
                        self._mark_closed('wall_budget_exhausted')
                deadline += period
                delay = deadline - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                elif delay < -period * 3:
                    deadline = time.monotonic()
        except Exception as exc:
            with self.lock:
                self.status = 'failed'
                if self.active:
                    self.segments[self.active]['cancel_requested'] = True
                self.event('runtime_failed', error=str(exc))
                self.cv.notify_all()
        finally:
            if self.backend:
                self.backend.close()

    def checkpoint(self, segment):
        with self.lock:
            if segment.get('cancel_requested') or segment['revision'] != self.revision:
                raise Halt('Cancelled or superseded goal', segment.get('halt_code', 'CANCELLED'), segment.get('halt_evidence'))
            if self.status != 'ready' or time.monotonic() - self.started >= self.limits['wall_seconds']:
                code = 'BUDGET_EXHAUSTED' if self.status == 'budget_exhausted' or time.monotonic() - self.started >= self.limits['wall_seconds'] else 'EPISODE_UNAVAILABLE'
                raise Halt('Episode unavailable or wall budget exhausted', code)

    def observe(self):
        with self.lock:
            result = copy.deepcopy(self.snapshot)
            result['goal'] = copy.deepcopy(self.goal)
            result['revision'] = self.revision
            result['runtime_alert'] = copy.deepcopy(self.runtime_alert)
            recovery_state = getattr(getattr(self, 'backend', None), 'public_recovery_state', None)
            if recovery_state is not None:
                result['bounded_recovery_state'] = plain(recovery_state())
            result['age_seconds'] = time.time() - result.get('captured_at', time.time())
            return result

    def interrupt_public_hold(self, report):
        """Internal monitor signal: fence decisions and request owner-confirmed hold.

        This is not a public tool or an emergency-stop guarantee. The physical
        owner switches the target; only its hold_confirmed event proves that.
        """
        with self.lock:
            if self.status != 'ready' or report.get('status') != 'violated':
                return False
            identity = (report.get('object_name'), report.get('arm', {}).get('control_step'))
            if self.runtime_alert and tuple(self.runtime_alert['identity']) == identity:
                return False
            self.revision += 1
            self.hold_serial += 1
            self.hold_requested = True
            self.monitor_requested_at = time.monotonic()
            evidence = plain(report)
            if self.active:
                self.segments[self.active].update(cancel_requested=True, halt_code='PUBLIC_HOLDING_LOSS', halt_evidence=evidence)
            self.runtime_alert = dict(identity=list(identity), code='PUBLIC_HOLDING_LOSS',
                revision=self.revision, hold_serial=self.hold_serial, hold_confirmed=False, evidence=evidence)
            self.snapshot['revision'] = self.revision
            event = self.event('public_holding_contradicted', **self.runtime_alert)
            self.runtime_alert['event_id'] = event['id']
            return True

    def decision(self, request_id, revision):
        with self.lock:
            key = 'decision:' + request_id
            if key in self.requests:
                return self.requests[key]
            if revision != self.revision:
                raise StaleRevision('Stale goal or scene revision')
            if self.status != 'ready':
                raise ValueError('Episode unavailable')
            if self.runtime_alert is not None and not self.runtime_alert['hold_confirmed']:
                raise HoldPending('Wait for owner-confirmed protective hold')
            if self.used['decisions'] >= self.limits['decisions']:
                raise ValueError('Decision budget exhausted')
            self.used['decisions'] += 1
            self.requests[key] = self.event('decision_admitted', request_id=request_id)['id']
            return self.requests[key]

    def execute(self, request_id, revision, subgoal, code, contract=None):
        if contract is not None:
            if not isinstance(contract, dict) or set(contract) - {'preconditions', 'postcondition'}:
                raise ValueError('Contract supports preconditions and postcondition only')
            preconditions = contract.get('preconditions', [])
            if not isinstance(preconditions, list) or len(preconditions) > 4:
                raise ValueError('At most four public preconditions are supported')
            contract = dict(preconditions=[validate_goal(g) for g in preconditions],
                            **({'postcondition': validate_goal(contract['postcondition'])} if 'postcondition' in contract else {}))
        digest = hashlib.sha256(json.dumps([revision, subgoal, code, contract], sort_keys=True).encode()).hexdigest()
        with self.lock:
            if request_id in self.requests:
                existing = self.requests[request_id]
                if existing['digest'] != digest:
                    raise ValueError('Idempotency key conflicts with original payload')
                return existing['segment_id']
            if revision != self.revision:
                raise StaleRevision('Stale goal or scene revision')
            if self.status != 'ready' or self.active:
                raise ValueError('Busy or unavailable episode')
            if self.runtime_alert is not None and not self.runtime_alert['hold_confirmed']:
                raise HoldPending('Wait for owner-confirmed protective hold')
            if not isinstance(code, str) or len(code) > 12000:
                raise ValueError('Invalid code')
            segment_id = identifier()
            segment = dict(id=segment_id, revision=revision, subgoal=subgoal, code=code,
                           contract=contract, precondition_results=[], api_attempts=[],
                           execution='running', effect='unknown', cancel_requested=False)
            self.segments[segment_id] = segment
            self.active = segment_id
            self.requests[request_id] = dict(digest=digest, segment_id=segment_id)
            self.event('segment_started', segment=segment)
            threading.Thread(target=self._execute, args=(segment,), daemon=True).start()
            return segment_id

    def _execute(self, segment):
        last_feedback = None
        def invoke(name, fn):
            def call(*args, **kwargs):
                nonlocal last_feedback
                with self.lock:
                    self.checkpoint(segment)
                    if self.used['calls'] >= self.limits['calls']:
                        raise Halt('Public API budget exhausted', 'BUDGET_EXHAUSTED')
                    self.used['calls'] += 1
                    call_id = identifier()
                    attempt = dict(call_id=call_id, name=name, args=plain(args), kwargs=plain(kwargs), status='running')
                    segment['api_attempts'].append(attempt)
                    self.event('api_started', call_id=call_id, name=name, args=args, kwargs=kwargs)
                try:
                    # Perception/IK RPCs can be slow. Abandon their wait on stop;
                    # their sensor facade is bound to this cancelled segment and
                    # can never submit a target for a later segment.
                    result = plain(self._cancellable_worker(segment, lambda: fn(*args, **kwargs)))
                    self.checkpoint(segment)
                    with self.lock:
                        self.checkpoint(segment)
                        attempt.update(status='completed', result={k: result[k] for k in
                            ('status', 'reason', 'views_used', 'total_views_used') if k in result}
                            if isinstance(result, dict) else None)
                    if name in {'goto_pose', 'home_pose', 'open_gripper', 'close_gripper'}:
                        with self.lock:
                            self.motion = False
                    self.event('api_result', call_id=call_id, name=name, result=result)
                    if isinstance(result, dict) and result.get('status') in {'failed', 'unknown'}:
                        raise EffectError(result)
                    return result
                except Exception as exc:
                    last_feedback = feedback(exc, api=name, call_id=call_id, observation=self.observe())
                    with self.lock:
                        attempt.update(status='cancelled' if isinstance(exc, Halt) else 'failed', feedback=last_feedback)
                    self.event('api_error', call_id=call_id, name=name, error=str(exc), feedback=last_feedback)
                    raise
            return call
        try:
            self.checkpoint(segment)
            for goal in (segment.get('contract') or {}).get('preconditions', []):
                result = self.verify(goal)
                segment['precondition_results'].append(dict(condition=goal, **result))
                self.event('precondition_checked', condition=goal, result=result)
                self.checkpoint(segment)
                if result['status'] != 'verified':
                    raise EffectError(result, phase='precondition')
            registered = self._cancellable_worker(segment, lambda: self.backend.tools(segment))
            tools = {name: invoke(name, fn) for name, fn in registered.items()}
            Interpreter(tools, lambda: self.checkpoint(segment)).run(segment['code'])
            self.checkpoint(segment)
            segment['execution'] = 'completed'
        except Halt as exc:
            segment.update(execution='cancelled', error=str(exc), feedback=last_feedback or feedback(exc, observation=self.observe()))
        except Exception as exc:
            segment.update(execution='failed', error=str(exc), feedback=last_feedback or feedback(exc, observation=self.observe()))
        finally:
            try:
                held = self._hold(wait=True, segment_id=segment['id'], cancel_execution=False)
                if not held['stopped']:
                    raise TimeoutError('Hold has not been confirmed')
            except Exception as exc:
                segment['stop_error'] = str(exc)
            condition = (segment.get('contract') or {}).get('postcondition')
            readiness = dict(status='not_required')
            if segment.get('stop_error'):
                readiness = dict(status='unknown', reason='hold_not_confirmed')
            elif segment['execution'] == 'completed':
                try:
                    readiness = self._wait_for_evidence(segment, [self.goal, *([condition] if condition else [])])
                except Halt as exc:
                    readiness = dict(status='cancelled', reason=str(exc), code=exc.code)
                    segment.update(execution='cancelled', error=str(exc), feedback=feedback(exc, observation=self.observe()))
                except Exception as exc:
                    readiness = dict(status='unknown', reason='evidence_readiness_unavailable', error=str(exc))
            with self.lock:
                if segment['execution'] == 'completed':
                    try:
                        self.checkpoint(segment)
                    except Halt as exc:
                        readiness = dict(status='cancelled', reason=str(exc), code=exc.code)
                        segment.update(execution='cancelled', error=str(exc), feedback=feedback(exc, observation=self.observe()))
                segment['verification_wait'] = readiness
                unavailable = readiness['status'] not in {'ready', 'not_required'}
                unknown = dict(status='unknown', reason='Post-action evidence unavailable', verification_wait=readiness)
                segment['goal_verification'] = unknown if unavailable else self.verify()
                segment['effect'] = segment['goal_verification']['status']
                if condition and segment['execution'] == 'completed':
                    result = unknown if unavailable else self.verify(condition)
                    segment['stage_verification'] = dict(condition=condition, **result)
                    self.event('postcondition_checked', condition=condition, result=result)
                    if result['status'] != 'verified':
                        segment['feedback'] = feedback(EffectError(result, 'postcondition'), observation=self.observe())
                else:
                    segment['stage_verification'] = dict(status='unknown', reason='Segment did not complete' if condition else 'No public postcondition declared')
                self.active = None
                self.event('segment_finished', segment=segment)

    def _cancellable_worker(self, segment, function):
        """Bound executor waits, including a backend that hangs during setup."""
        future = concurrent.futures.Future()
        def work():
            try:
                future.set_result(function())
            except BaseException as exc:
                future.set_exception(exc)
        threading.Thread(target=work, daemon=True).start()
        while not future.done():
            self.checkpoint(segment)
            try:
                future.result(timeout=.05)
            except concurrent.futures.TimeoutError:
                pass
        self.checkpoint(segment)
        return future.result()

    def _wait_for_evidence(self, segment, goals):
        readiness = getattr(self.backend, 'verification_readiness', None)
        if readiness is None:
            return dict(status='not_required', reason='backend_has_no_temporal_window')
        started = time.monotonic()
        announced = False
        with self.cv:
            while True:
                try:
                    self.checkpoint(segment)
                    result = plain(readiness(goals, copy.deepcopy(self.snapshot)))
                    if result.get('status') not in {'ready', 'pending'}:
                        raise ValueError('Invalid evidence-readiness result')
                except Exception as exc:
                    if announced:
                        self.event('verification_wait_finished', result=dict(
                            status='cancelled' if isinstance(exc, Halt) else 'unknown',
                            reason=str(exc), elapsed_seconds=time.monotonic()-started))
                    raise
                elapsed = time.monotonic()-started
                if result['status'] == 'ready':
                    final = dict(result, elapsed_seconds=elapsed)
                    if announced:
                        self.event('verification_wait_finished', result=final)
                    return final
                if not announced:
                    self.event('verification_wait_started', readiness=result,
                               timeout_seconds=POST_ACTION_EVIDENCE_TIMEOUT_SECONDS)
                    announced = True
                remaining = POST_ACTION_EVIDENCE_TIMEOUT_SECONDS-elapsed
                if remaining <= 0:
                    final = dict(result, status='timed_out', elapsed_seconds=elapsed)
                    self.event('verification_wait_finished', result=final)
                    return final
                self.cv.wait(min(.05, remaining))

    def cancel(self, wait=True, segment_id=None):
        return self._hold(wait, segment_id, cancel_execution=True)

    def _hold(self, wait=True, segment_id=None, cancel_execution=True):
        with self.cv:
            if self.status == 'closed':
                return dict(stopped=self.hold_serial > 0 and self.hold_ack >= self.hold_serial,
                            hold_serial=self.hold_ack)
            if segment_id and self.active != segment_id:
                return dict(stopped=self.active is None)
            requested = time.monotonic()
            if self.active and cancel_execution:
                self.segments[self.active]['cancel_requested'] = True
            self.hold_serial += 1
            serial = self.hold_serial
            self.hold_requested = True
            self.event('cancel_requested' if cancel_execution else 'hold_requested', hold_serial=serial)
            if wait:
                if not self.cv.wait_for(lambda: self.hold_ack >= serial or self.status == 'failed', timeout=5):
                    raise TimeoutError('Hold has not been confirmed')
            confirmed = self.hold_ack >= serial
            if confirmed:
                self.event('stop_confirmed', latency_seconds=time.monotonic() - requested)
            return dict(stopped=confirmed, hold_serial=serial)

    def update_goal(self, goal):
        goal = validate_goal(goal)
        with self.lock:
            if self.status not in {'ready', 'initializing'}:
                raise ValueError('Episode unavailable; cannot change a terminal goal')
        self.cancel()
        with self.lock:
            if self.status != 'ready':
                raise ValueError('Episode unavailable; cannot change a terminal goal')
            self.goal = goal
            self.revision += 1
            self.event('goal_changed', revision=self.revision, goal=goal)
            return self.state()

    def verify(self, goal=None):
        with self.lock:
            if self.runtime_alert is not None and not self.runtime_alert['hold_confirmed']:
                return dict(status='unknown', reason='Protective hold not confirmed')
            if not self.snapshot or time.time() - self.snapshot['captured_at'] > 1:
                return dict(status='unknown', reason='Missing or stale observation')
            try:
                result = plain(self.backend.verify(self.goal if goal is None else goal, copy.deepcopy(self.snapshot)))
                if result.get('status') not in {'verified', 'failed', 'unknown'}:
                    raise ValueError('Invalid verifier result')
            except Exception as exc:
                self.event('verification_error', error=str(exc))
                return dict(status='unknown', reason='Verifier unavailable', error=str(exc))
            return result

    def finish(self, revision):
        with self.lock:
            if revision != self.revision:
                raise StaleRevision('Stale goal or scene revision')
            if self.active:
                raise ValueError('Task has an active segment')
            result = self.verify()
            self.event('finish_checked', result=result)
            return result

    def close(self):
        with self.lock:
            if self.status == 'closed':
                return
        if self.thread.is_alive():
            self.cancel()
        with self.lock:
            if self.active:
                self.segments[self.active]['cancel_requested'] = True
            self._mark_closed('explicit_close')
        self.thread.join(timeout=10)

    def _mark_closed(self, reason):
        # Called only after protective hold by the owner or explicit close.
        # Use the same terminal transition so repeat close never adds cost.
        if self.status == 'closed':
            return
        self.exit = True
        if self.ended is None:
            self.ended = time.monotonic()
        self.status = 'closed'
        self.termination_reason = reason
        self.event('episode_closed', reason=reason, hold_confirmed=self.hold_serial > 0 and self.hold_ack >= self.hold_serial)


class Manager:
    def __init__(self, root, factory):
        self.root, self.factory = Path(root), factory
        self.root.mkdir(parents=True, exist_ok=True)
        self.episodes, self.keys = {}, {}
        self.lock = threading.RLock()
        # Never recreate a physical scene or replay code after service restart.
        for file in self.root.glob('*/episode.json'):
            data = json.loads(file.read_text(encoding='utf-8'))
            if data['status'] != 'closed':
                data['status'] = 'interrupted'
                for segment in data['segments'].values():
                    if segment['execution'] == 'running':
                        segment['execution'] = 'cancelled'
                        segment['error'] = 'Service restarted; no replay'
                file.write_text(json.dumps(data), encoding='utf-8')
        index = self.root / 'requests.json'
        if index.exists():
            self.keys = json.loads(index.read_text())

    def create(self, request_id, task, seed, goal, limits=None):
        with self.lock:
            if not isinstance(request_id, str) or not 1 <= len(request_id) <= 128:
                raise ValueError('request_id must be a 1..128 character string')
            payload = dict(task=task, seed=seed, goal=goal, limits=limits)
            if request_id in self.keys:
                prior = self.keys[request_id]
                if prior['payload'] != payload:
                    raise ValueError('Creation key conflicts')
                return self.get(prior['id'])
            # Stable admission identity survives a crash between episode.json
            # and the secondary request index commit.
            episode_id = hashlib.sha256(request_id.encode()).hexdigest()[:32]
            file = self.root / episode_id / 'episode.json'
            if file.exists():
                prior = self.get(episode_id)
                if prior.get('creation', {}).get('payload') != payload:
                    raise ValueError('Creation key conflicts with persisted episode')
                return prior
            if any(e.status not in {'closed', 'failed'} for e in self.episodes.values()):
                raise ValueError('Close current episode first')
            episode = Episode(self.root, task, seed, goal, self.factory, limits,
                              episode_id=episode_id, creation=dict(request_id=request_id, payload=payload))
            self.episodes[episode.id] = episode
            self.keys[request_id] = dict(id=episode.id, payload=payload)
            temp = self.root / 'requests.tmp'
            temp.write_text(json.dumps(self.keys), encoding='utf-8')
            temp.replace(self.root / 'requests.json')
            return episode.state()

    def get(self, episode_id):
        if episode_id in self.episodes:
            return self.episodes[episode_id].state()
        if not isinstance(episode_id, str) or len(episode_id) != 32 or any(c not in '0123456789abcdef' for c in episode_id):
            raise ValueError('Invalid episode ID')
        return json.loads((self.root / episode_id / 'episode.json').read_text())
