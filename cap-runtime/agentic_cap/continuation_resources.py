"""Conditional registered completion paths built only from Runtime task data.

No future fact is declared true. A cost bound excludes a registered option;
it never proves that all CaP programs or physical strategies are impossible.
"""
import copy


def completion_paths(nodes, ready_nodes, remaining):
    by_id = {node['id']: node for node in nodes}
    paths = []
    for start_id in ready_nodes:
        if start_id not in by_id:
            continue
        steps, skipped, visited = [], [], set()
        current = start_id
        unknown = None
        terminal = False
        while current:
            if current in visited or current not in by_id:
                unknown = 'cyclic_or_missing_registered_continuation'
                break
            visited.add(current)
            node = by_id[current]
            future = current != start_id
            continuation = node.get('continuation', {})
            if future and node['status'] in {'satisfied', 'not_required'}:
                skipped.append(dict(task_node=current, status=node['status'],
                    assumption='current_satisfaction_must_persist_or_the_path_is_invalid'))
            else:
                routes = node.get('resources', {}).get('skills', {})
                estimate = next(iter(routes.values())) if len(routes) == 1 else None
                skill = next(iter(routes)) if len(routes) == 1 else None
                step = dict(task_node=current, kind=node['kind'], skill=skill,
                    execution='current_candidate' if not future else 'deferred_not_authorized',
                    observed_status=node['status'], requirements=copy.deepcopy(node.get('requirements', [])),
                    resources=copy.deepcopy(estimate),
                    estimate_source='currently_compiled_registered_program' if estimate else 'unavailable')
                steps.append(step)
            if continuation.get('goal_if_public_postcondition_verified'):
                terminal = True
                break
            if not continuation.get('next_node'):
                unknown = continuation.get('unknown_reason', 'no_registered_success_continuation')
                break
            current = continuation['next_node']
        def valid_cost(cost):
            return (cost is not None and all(type(cost.get(k)) is int for k in
                ('minimum_calls','maximum_calls','required_calls'))
                and 0 <= cost['minimum_calls'] <= cost['maximum_calls'] <= cost['required_calls'])
        complete_cost = terminal and all(valid_cost(step['resources']) for step in steps)
        minimum = maximum = required = None
        if complete_cost:
            minimum = maximum = required = 0
            for step in steps:
                cost = step['resources']
                required = max(required, maximum+cost['required_calls'])
                minimum += cost['minimum_calls']
                maximum += cost['maximum_calls']
            required = max(required, maximum)
        calls = remaining.get('calls')
        status = ('unknown' if not complete_cost or not isinstance(calls, (int,float)) else
                  'insufficient_registered_path' if calls < minimum else
                  'conditional_branch_budget' if calls < required else 'conditional')
        restore = by_id[start_id].get('purpose') == 'restore_recorded_pose'
        paths.append(dict(start_node=start_id, current_action_role=by_id[start_id].get('purpose', 'goal_progress'),
            completion_budget_must_not_suppress_current_action=restore,
            steps=steps, skipped_currently_satisfied=skipped,
            reaches_goal_if_all_declared_effects_verify=terminal,
            minimum_calls=minimum, maximum_calls=maximum, required_calls=required,
            remaining_calls=calls, status=status,
            future_model_requests_if_one_stage_per_reply=max(0,len(steps)-1),
            unknown_reason=unknown or ('future_route_cannot_be_compiled_from_current_public_state' if not complete_cost else None),
            assumptions=['Every future stage requires a new observation and Runtime admission.',
                         'Each declared success effect must be publicly verified; failed effects invalidate the path.',
                         'Changed geometry may invalidate the currently compiled cost profile.',
                         'Current satisfied stages must remain satisfied; new sensing/recovery costs are not included.'],
            authorization='advisory_registered_path_only; no queued or automatic actions',
            scope='Stage-chain API bounds, not all-program impossibility, success probability, wall/motion prediction or a universal planner'))
    return paths
