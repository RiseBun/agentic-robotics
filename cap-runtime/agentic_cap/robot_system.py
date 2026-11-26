"""Runtime-owned public facts, task dependencies and execution projection.

This module receives only public sensor packets and the existing public verifier.
It never reads simulator truth, files, model text or inferred task completion.
"""
import copy
import hashlib
import json
import math
import time

from .skills import catalogue, holding_geometry, valid_vector, selected_support_geometry, rotation_clearance
from .task_recipes import build_task
from .program_resources import project_resources
from .continuation_resources import completion_paths

VERSION = 1
MAX_AGE_SECONDS = 1.


class TaskExecutive:
    """One lifecycle policy for bound code and registered skills.

    The Episode owns the execution ledger and lock. This policy reads that
    ledger rather than maintaining another completion or retry database.
    Current satisfaction and historical attempt outcome remain separate.
    """
    @staticmethod
    def outcome(segment):
        category = (segment.get('feedback') or {}).get('category')
        execution = segment['execution']
        effect = segment.get('node_verification', {}).get('status', 'unknown')
        if segment.get('stop_error'):
            phase, action = 'hold_unconfirmed', 'await_protective_hold'
        elif category == 'public_holding_loss':
            phase, action = 'holding_lost', 'reassess_current_goal'
        elif category in {'interface_timeout', 'episode_unavailable'}:
            phase, action = 'interface_unavailable', 'check_interface_or_ask_user'
        elif execution == 'cancelled':
            phase, action = ('budget_exhausted', 'end_task') if category == 'budget_exhausted' else ('cancelled', 'reassess_current_goal')
        elif execution == 'completed' and effect == 'verified':
            phase, action = 'verified', 'continue_task'
        elif category == 'resources_insufficient':
            phase, action = 'resources_insufficient', 'choose_affordable_program_or_ask_user'
        elif category == 'invalid_program':
            phase, action = 'program_rejected', 'revise_program'
        elif category == 'insufficient_evidence' or (execution == 'completed' and effect == 'unknown'):
            phase, action = 'evidence_insufficient', 'acquire_evidence_or_use_authorized_recovery'
        else:
            phase, action = 'effect_failed' if effect == 'failed' else 'execution_failed', 'recover_or_replan'
        return dict(phase=phase, next_action=action, execution=execution, node_effect=effect,
                    feedback_category=category, causal_status='not_established')

    @staticmethod
    def admit(system, node):
        if node.get('route') == 'verification':
            raise ValueError('Verification-only node does not authorize robot actions')
        if (not system['world']['fresh'] or node['status'] != 'ready'
                or system['executor'].get('hold_pending')
                or system['task']['execution']['phase'] in {'budget_exhausted', 'awaiting_user', 'holding'}):
            raise ValueError('Task node dependencies are not currently ready: '+node['id'])
        if system['executor'].get('active_segment'):
            raise ValueError('Another task attempt is still running')
        return dict(node_id=node['id'], goal_version=system['goal_version'],
                    admitted_state_id=system['state_id'], requirements=copy.deepcopy(node['requirements']),
                    postconditions=copy.deepcopy(node['postconditions']),
                    retry_context=copy.deepcopy(node.get('retry_context')))

    @classmethod
    def project(cls, nodes, facts, segments, *, goal_version, active, status, fresh, hold_pending, assistance=None, resources=None):
        current = [s for s in segments.values() if s.get('task_binding', {}).get('goal_version') == goal_version]
        for node in nodes:
            attempts = [s for s in current if s['task_binding']['node_id'] == node['id']]
            last = attempts[-1] if attempts else None
            node['lifecycle'] = dict(attempt_count=len(attempts),
                current_satisfied=node['status'] == 'satisfied',
                last_attempt_id=last['id'] if last else None,
                last_outcome=copy.deepcopy(last.get('task_outcome')) if last else None)
        latest = current[-1] if current else None
        ready = [n['id'] for n in nodes if n['status'] == 'ready' and n.get('route') != 'verification'] if fresh and status == 'ready' and not hold_pending and not active else []
        if status != 'ready':
            phase, action = 'unavailable', 'end_task' if status in {'closed', 'interrupted', 'failed', 'budget_exhausted'} else 'wait_for_runtime'
        elif hold_pending:
            phase, action = 'holding', 'await_protective_hold'
        elif assistance:
            phase, action, ready = ('awaiting_user', 'wait_for_user_instruction', []) if assistance.get('hold_confirmed') else ('holding', 'await_protective_hold', [])
        elif active:
            phase, action = 'running', 'wait_for_attempt'
        elif latest and latest.get('task_outcome', {}).get('phase') == 'budget_exhausted':
            phase, action, ready = 'budget_exhausted', 'end_task', []
        elif not fresh:
            phase, action = 'state_unavailable', 'observe_or_ask_user'
        elif facts['goal']['status'] == 'verified':
            phase, action = 'verified', 'request_final_verification'
        elif resources is not None and resources['remaining'].get('calls', 0) <= 0:
            phase, action, ready = 'budget_exhausted', 'end_task', []
        elif any(n['status'] == 'awaiting_verification' for n in nodes):
            phase, action = 'awaiting_verification', 'acquire_evidence'
        elif any(n['kind'] == 'recover' and n['status'] == 'ready' for n in nodes):
            phase, action = 'recovery_ready', 'choose_authorized_recovery'
        elif latest and latest.get('task_outcome', {}).get('phase') == 'program_rejected' and latest['task_binding']['node_id'] in ready:
            phase, action = 'program_rejected', 'revise_program'
        elif ready:
            phase, action = 'ready', 'choose_ready_node'
        elif any(n.get('retry_constraint') and n['status'] == 'blocked' for n in nodes):
            phase, action = 'placement_route_blocked', 'change_placement_evidence_or_ask_user'
        elif latest and latest.get('task_outcome', {}).get('phase') not in {None, 'verified'}:
            outcome = latest['task_outcome']
            phase, action = outcome['phase'], outcome['next_action']
        else:
            phase, action = 'blocked', 'observe_or_ask_user'
        affordable, resource_blocked = [], []
        for node in nodes:
            if node['id'] not in ready:
                continue
            for skill, estimate in node.get('resources', {}).get('skills', {}).items():
                route = dict(task_node=node['id'], skill=skill, resources=copy.deepcopy(estimate))
                if estimate['status'] == 'fits':
                    affordable.append(route)
                elif estimate['status'] == 'insufficient':
                    resource_blocked.append(route)
        restore = [dict(route, role='restore_recorded_pose',
                        reason='reverse_the_authorized_recorded_descent_before_optional_work')
                   for route in affordable
                   if next(n for n in nodes if n['id']==route['task_node']).get('purpose')=='restore_recorded_pose']
        return dict(phase=phase, next_action=action, ready_nodes=ready,
                    priority_restore_routes=restore,
                    affordable_skill_routes=affordable, resource_blocked_skill_routes=resource_blocked,
                    cap_program_cost='actual_program_preflight_required',
                    last_attempt_id=latest['id'] if latest else None,
                    last_outcome=copy.deepcopy(latest.get('task_outcome')) if latest else None,
                    active_segment=active, automatic_retry=False)


def condition_key(condition):
    if 'holding' in condition:
        return 'holding:'+condition['holding']
    if 'on' in condition:
        return 'on:'+':'.join(condition['on'])
    return 'empty_gripper'


class RobotSystem:
    def __init__(self, episode_id):
        self.episode_id = episode_id
        self.goal_version = 1
        self.latest = None
        self.cuts = {}

    def change_goal(self):
        self.goal_version += 1
        self.latest = None
        # Keep historical references for audit, but never reuse their authority.

    def publish(self, snapshot, goal, revision, verify, *, status, active, segments, alert=None, hold_pending=False, assistance=None, budget=None):
        stamp = snapshot.get('captured_at')
        now = time.time()
        fresh = (isinstance(stamp, (int, float)) and not isinstance(stamp, bool)
                 and 0 <= now-stamp <= MAX_AGE_SECONDS and status == 'ready')
        pending_hold = hold_pending or bool(alert and not alert.get('hold_confirmed'))
        source = dict(evidence_ref=snapshot.get('evidence_ref'), captured_at=stamp,
                      control_step=snapshot.get('control_step'), goal_version=self.goal_version,
                      revision=revision)
        facts = {}

        def fact(key, result):
            result = copy.deepcopy(result)
            if result.get('status') not in {'verified', 'failed', 'unknown'}:
                result = dict(status='unknown', reason='invalid_public_verifier_result')
            if not fresh:
                result = dict(status='unknown', reason='stale_or_unavailable_public_state')
            elif pending_hold:
                result = dict(status='unknown', reason='protective_hold_not_confirmed')
            facts[key] = dict(status=result['status'], source=copy.deepcopy(source), result=result)

        def check(key, condition):
            try:
                result = verify(condition, copy.deepcopy(snapshot)) if fresh else dict(status='unknown')
            except Exception:
                result = dict(status='unknown', reason='public_verifier_unavailable')
            fact(key, result)

        for name in ['red cube', 'green cube']:
            check('holding:'+name, dict(holding=name))
            geometry = holding_geometry(facts['holding:'+name]['result'])
            fact('holding_geometry:'+name, dict(
                status='verified' if facts['holding:'+name]['status'] == 'verified' and geometry else 'unknown',
                reason='current_public_holding_offset_and_orientation', geometry=geometry))
            obj = snapshot.get('objects', {}).get(name, {})
            fact('visible:'+name, dict(status='verified' if obj.get('status') == 'visible' else 'unknown',
                                       reason='current_public_visibility'))
            span = obj.get('surface_span_xyz')
            extent = (isinstance(span, list) and len(span) == 3
                      and all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0 for x in span)
                      and .015 <= max(span) <= .08 and obj.get('status') == 'visible')
            fact('extent:'+name, dict(status='verified' if extent or snapshot.get('backend') == 'fixture' else 'unknown',
                                      reason='current_public_cube_extent; fixture_is_protocol_only'))
            support = snapshot.get('support_evidence', {}).get(name, {})
            # A visible centre is not sufficient support-size evidence.
            fact('support:'+name, dict(support, status='verified' if support.get('status') == 'available' else 'unknown'))
            support_surface = selected_support_geometry(snapshot, name)
            lo, hi = support_surface.get('surface_q05_xyz'), support_surface.get('surface_q95_xyz')
            valid = valid_vector(lo, 3) and valid_vector(hi, 3) and all(a <= b for a, b in zip(lo, hi))
            fact('support_geometry:'+name, dict(
                status='verified' if valid and support_surface.get('status') == 'visible' else 'unknown',
                evidence_ref=support.get('evidence_ref', snapshot.get('evidence_ref')),
                camera_name=support.get('camera_name', 'robot0_robotview'),
                reason='finite_ordered_current_public_surface_bounds'))
        check('empty_gripper', dict(empty_gripper=True))
        for relation in [['red cube', 'green cube'], ['green cube', 'red cube']]:
            check('on:'+':'.join(relation), dict(on=relation))
        check('goal', goal)
        release = snapshot.get('bounded_recovery_state', {}).get('placement_session') or {}
        fact('release_recorded', dict(status='verified' if release.get('current_goal') and release.get('release_id') else 'unknown',
                                     reason='current_goal_public_release_receipt; not physical completion'))
        route = snapshot.get('recovery_options', {}).get('vertical_retreat') or {}
        hint = snapshot.get('bounded_recovery_state', {}).get('holding_identity_hint')
        target, quat = route.get('target_xyz'), route.get('target_wxyz')
        valid_route = (route.get('status') == 'available' and valid_vector(target, 3)
                       and valid_vector(quat, 4) and sum(x*x for x in quat) >= 1e-16
                       and snapshot.get('last_gripper_command') == 'closed'
                       and not (alert and alert.get('code') == 'PUBLIC_HOLDING_LOSS'))
        for name in ['red cube', 'green cube']:
            fact('retreat_path:'+name, dict(status='verified' if valid_route and hint == name else 'unknown',
                reason='current_recorded_descent_reversal_authorization; not current holding proof', route=copy.deepcopy(route)))
        for name in ['red cube', 'green cube']:
            fact('rotation_clearance:'+name, dict(status='verified' if rotation_clearance(snapshot,name) else 'unknown',
                reason='bounded_two_cube_high_clearance; not general collision certification'))
        if alert and alert.get('code') == 'PUBLIC_HOLDING_LOSS':
            name = (alert.get('evidence') or {}).get('object_name')
            if name in {'red cube', 'green cube'}:
                fact('holding:'+name, dict(status='failed', reason='public_holding_monitor_contradiction'))
                fact('holding_geometry:'+name, dict(status='unknown', reason='public_holding_monitor_contradiction'))
        atom_keys = (['holding:'+goal['holding']] if goal.get('holding') else [])
        atom_keys += ['on:'+':'.join(goal['on'])] if goal.get('on') and goal['on'][1] != 'table' else []
        atom_keys += ['empty_gripper'] if goal.get('empty_gripper') else []
        atom_statuses = [facts[key]['status'] for key in atom_keys]
        if atom_statuses and not (goal.get('on') and goal['on'][1] == 'table'):
            combined = 'failed' if 'failed' in atom_statuses else 'verified' if all(s == 'verified' for s in atom_statuses) else 'unknown'
            if combined != facts['goal']['status']:
                fact('goal', dict(status=combined, reason='current_public_fact_conjunction',
                                  evidence=[facts[key]['result'] for key in atom_keys]))
        recipe, nodes = build_task(goal, facts, snapshot, segments, self.goal_version)
        if active in segments:
            binding = segments[active].get('task_binding')
            for node in nodes:
                if binding and node['id'] == binding['node_id']:
                    node['status'] = 'running'
        resources = project_resources(nodes, facts, snapshot, budget)
        management = TaskExecutive.project(nodes, facts, segments, goal_version=self.goal_version,
                                           active=active, status=status, fresh=fresh, hold_pending=pending_hold, assistance=assistance, resources=resources)
        if resources is not None:
            resources['completion_paths'] = completion_paths(nodes, management['ready_nodes'], resources['remaining'])
        body = dict(episode_id=self.episode_id, goal_version=self.goal_version, revision=revision,
                    control_step=source['control_step'], evidence_ref=source['evidence_ref'],
                    facts={key: value['status'] for key, value in facts.items()},
                    remaining_API_calls=resources['remaining'].get('calls') if resources else None)
        state_id = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
        self.cuts[state_id] = dict(goal_version=self.goal_version, revision=revision)
        if len(self.cuts) > 512:
            del self.cuts[next(iter(self.cuts))]
        self.latest = dict(contract_version=VERSION, state_id=state_id, episode_id=self.episode_id,
                           goal_version=self.goal_version, revision=revision,
                           world=dict(source=source, fresh=fresh, age_seconds=now-stamp if isinstance(stamp, (int, float)) and math.isfinite(stamp) else None, facts=facts),
                           resources=resources,
                           task=dict(goal=copy.deepcopy(goal), status='verified' if facts['goal']['status'] == 'verified' else 'open',
                                     nodes=nodes, recipe=recipe, execution=management,
                                     assistance=copy.deepcopy(assistance)), skills=catalogue(snapshot),
                           executor=dict(status=status, active_segment=active,
                                         hold_pending=pending_hold,
                                         mode='skill' if active and segments[active].get('skill') else 'cap_program' if active else 'idle',
                                         alert=copy.deepcopy(alert)))
        return copy.deepcopy(self.latest)

    def view(self, revision, status, active=None, segment=None, assistance=None, *, remaining_calls=None, hold_pending=None):
        value = copy.deepcopy(self.latest)
        if value is None:
            return None
        stamp = value['world']['source']['captured_at']
        fresh = (isinstance(stamp, (int, float)) and 0 <= time.time()-stamp <= MAX_AGE_SECONDS
                 and value['revision'] == revision and status == 'ready')
        value['world']['fresh'] = fresh
        value['world']['age_seconds'] = time.time()-stamp if isinstance(stamp, (int, float)) and math.isfinite(stamp) else None
        value['executor']['status'] = status
        value['executor']['active_segment'] = active
        if hold_pending is not None:
            value['executor']['hold_pending'] = hold_pending
        value['executor']['mode'] = 'skill' if segment and segment.get('skill') else 'cap_program' if active else 'idle'
        management = value['task'].setdefault('execution', {})
        management['active_segment'] = active
        value['task']['assistance'] = copy.deepcopy(assistance)
        if assistance:
            management.update(phase='awaiting_user' if assistance['status'] == 'awaiting_user' else 'holding',
                              next_action='wait_for_user_instruction', ready_nodes=[])
        binding = (segment or {}).get('task_binding')
        if fresh and active and binding and not assistance:
            management.update(phase='running', next_action='wait_for_attempt',
                              ready_nodes=[n for n in management.get('ready_nodes', []) if n != binding['node_id']])
            for node in value['task']['nodes']:
                if node['id'] == binding['node_id']:
                    node['status'] = 'running'
        if not fresh:
            for fact in value['world']['facts'].values():
                fact.update(status='unknown', result=dict(status='unknown', reason='stale_or_unavailable_public_state'))
            value['task']['status'] = 'unknown'
            for node in value['task']['nodes']:
                node['status'] = 'unknown'
                node.setdefault('lifecycle', {})['current_satisfied'] = False
            value['task']['execution'].update(phase='unavailable' if status != 'ready' else 'state_unavailable',
                next_action='end_task' if status in {'closed', 'interrupted', 'failed'} else 'observe_or_ask_user', ready_nodes=[])
        resources = value.get('resources')
        changed_budget = (resources is not None and remaining_calls is not None
                          and resources['remaining'].get('calls') != remaining_calls)
        if resources is not None and (not fresh or active or assistance or value['executor'].get('hold_pending') or changed_budget):
            resources['completion_paths'] = []
            resources['completion_paths_unavailable_reason'] = 'cached_cut_not_an_idle_current_budget_candidate'
            management['priority_restore_routes'] = []
            management['affordable_skill_routes'] = []
            management['resource_blocked_skill_routes'] = []
        return value

    def bind(self, state_id, node_id, revision):
        cut = self.cuts.get(state_id)
        if not cut or cut != dict(goal_version=self.goal_version, revision=revision):
            raise ValueError('Task state reference is missing or superseded')
        node = next((n for n in self.latest['task']['nodes'] if n['id'] == node_id), None)
        if node is None:
            raise ValueError('Task node is not part of the current goal')
        return copy.deepcopy(node)
