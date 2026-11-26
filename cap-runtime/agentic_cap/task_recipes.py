"""Runtime-owned task recipes; data, not model-supplied executable planning.

Facts authorize actions. Dependency edges explain the intended workflow, not a
requirement to repeat an already satisfied physical state in chronological order.
"""
import copy

from .skills import DEFINITIONS, catalogue, requirements_for
from .placement_retry import placement_context, retry_constraint


ACQUIRE = dict(kind='acquire', title='获取并确认持有{object}',
    arguments=dict(object='{object}'), skill='acquire_object',
    effects=[('holding:{object}', 'verified')], after_release='awaiting_verification')
VERIFY = dict(kind='verify', title='验收最终目标', effects=[('goal', 'verified')])
RECIPES = {
    'hold_object_v1': [ACQUIRE, dict(VERIFY, depends_on=['acquire'])],
    'stack_objects_v1': [
        dict(kind='clear_acquire', title='获取并确认持有遮挡物{support}',
             arguments=dict(object='{support}'), skill='acquire_object',
             requires=[('on:{support}:{object}', 'verified')],
             effects=[('holding:{support}', 'verified')],
             skip_when=[('on:{support}:{object}', 'failed'), ('empty_gripper', 'verified')]),
        dict(kind='clear', title='移开目标物上的遮挡物',
             arguments=dict(object='{support}', obstructed_object='{object}'),
             requires=[('holding:{support}', 'verified')], depends_on=['clear_acquire'],
             effects=[('on:{support}:{object}', 'failed'), ('empty_gripper', 'verified')],
             route='bounded_cap_program'),
        dict(ACQUIRE, requires=[('on:{support}:{object}', 'failed')], depends_on=['clear']),
        dict(kind='support', title='确认支撑物证据', skill='inspect_support',
             arguments=dict(object='{object}', support='{support}'), depends_on=['acquire'],
             effects=[('holding:{object}', 'verified'), ('support:{support}', 'verified')],
             satisfied_by=[('support:{support}', 'verified')], after_release='awaiting_verification'),
        dict(kind='place', title='放置、释放并撤离', skill='place_object',
             arguments=dict(object='{object}', support='{support}'), depends_on=['acquire', 'support'],
             effects=[('goal', 'verified')], after_release='awaiting_verification'),
        dict(kind='recover', title='沿已记录下降路径撤回并重新确认持有', skill='retreat_from_descent',
             arguments=dict(object='{object}'), depends_on=['place'], requires_verified_attempt=True,
             satisfied_by=[('holding:{object}', 'verified'), ('retreat_path:{object}', 'unknown')],
             effects=[('holding:{object}', 'verified')], optional_authorization='retreat_path:{object}'),
        dict(VERIFY, skill='inspect_placement', arguments=dict(object='{object}', support='{support}'),
             depends_on=['place']),
    ],
    # Accepted public predicates do not imply an available actuation recipe.
    'verify_existing_goal_v1': [VERIFY],
}


def validate_recipe(specs):
    kinds = [s['kind'] for s in specs]
    if len(kinds) != len(set(kinds)):
        raise ValueError('Duplicate task node kind')
    graph = {s['kind']: s.get('depends_on', []) for s in specs}
    visiting, visited = set(), set()
    def visit(kind):
        if kind not in graph or kind in visiting:
            raise ValueError('Missing or cyclic task dependency')
        if kind in visited:
            return
        visiting.add(kind)
        for parent in graph[kind]:
            visit(parent)
        visiting.remove(kind)
        visited.add(kind)
    for spec in specs:
        visit(spec['kind'])
        skill = spec.get('skill')
        if skill and (skill not in DEFINITIONS or spec['kind'] not in DEFINITIONS[skill]['nodes']):
            raise ValueError('Skill not registered for recipe node')
        if not spec.get('effects'):
            raise ValueError('Task nodes require Runtime-owned postconditions')


def select_recipe(goal):
    obj = goal.get('holding') or (goal.get('on') or [None])[0]
    support = (goal.get('on') or [None, None])[1]
    name = ('stack_objects_v1' if support in {'red cube', 'green cube'} else
            'hold_object_v1' if goal.get('holding') and not goal.get('on') else 'verify_existing_goal_v1')
    return name, dict(object=obj, support=support)


def build_task(goal, facts, snapshot, segments, goal_version):
    name, bindings = select_recipe(goal)
    specs = RECIPES[name]
    validate_recipe(specs)
    available = {s['name'] for s in catalogue(snapshot)}
    complete = facts.get('goal', {}).get('status') == 'verified'
    release = snapshot.get('bounded_recovery_state', {}).get('placement_session') or {}
    released = bool(release.get('current_goal') and release.get('release_id'))
    def predicates(items):
        return [(key.format(**bindings), expected) for key, expected in items]
    def actual(key):
        return facts.get(key, {}).get('status', 'unknown')
    def matches(items):
        return bool(items) and all(actual(key) == expected for key, expected in items)
    nodes = []
    for spec in specs:
        kind, skill = spec['kind'], spec.get('skill')
        identity = f'{goal_version}:{kind}'
        attempts = [s for s in segments.values() if s.get('task_binding', {}).get('node_id') == identity]
        attempt = attempts[-1] if attempts else None
        args = {k: v.format(**bindings) for k, v in spec.get('arguments', {}).items()}
        requirements = (requirements_for(skill, args, snapshot) if skill else []) + predicates(spec.get('requires', []))
        effects = predicates(spec['effects'])
        satisfied = matches(predicates(spec.get('satisfied_by', spec['effects'])))
        if spec.get('requires_verified_attempt'):
            satisfied = satisfied and any(s.get('node_verification', {}).get('status') == 'verified' for s in attempts)
        state = 'satisfied' if satisfied else 'ready' if all(actual(k) == v for k, v in requirements) else 'blocked'
        if matches(predicates(spec.get('skip_when', []))):
            state = 'not_required'
        authorization = spec.get('optional_authorization')
        if authorization and actual(authorization.format(**bindings)) != 'verified' and not attempt:
            state = 'not_required'
        if complete and kind != 'verify':
            state = 'not_required'
        elif released and spec.get('after_release'):
            state = spec['after_release']
        context = placement_context(snapshot, facts, args, segments) if kind == 'place' else None
        constraint = retry_constraint(attempts, context, bindings['support']) if kind == 'place' else None
        if constraint and state == 'ready':
            state = 'blocked'
        nodes.append(dict(id=identity, kind=kind, title=spec['title'].format(**bindings), status=state,
            depends_on=[f'{goal_version}:{k}' for k in spec.get('depends_on', [])],
            requirements=[dict(fact=k, expected=v, actual=actual(k)) for k, v in requirements],
            arguments=args, retry_context=context, retry_constraint=constraint, postconditions=[dict(fact=k, expected=v) for k, v in effects],
            skills=[skill] if skill in available else [], route=spec.get('route', 'registered_skill' if skill else 'verification'),
            last_attempt=dict(segment_id=attempt['id'], execution=attempt['execution'], effect=attempt.get('effect', 'unknown'),
                node_effect=attempt.get('node_verification', {}).get('status', 'unknown'),
                skill_effect=attempt.get('skill_verification', {}).get('status', 'unknown')) if attempt else None))
    place=next((n for n in nodes if n['kind']=='place'),None)
    if 'reorient_held_object' in available and place:
        identity=f'{goal_version}:reorient'
        attempts=[s for s in segments.values() if (s.get('task_binding') or {}).get('node_id')==identity]
        args=dict(object=bindings['object'])
        requirements=requirements_for('reorient_held_object',args,snapshot)
        ordered=list(segments.values())
        releases=[s for s in ordered if (s.get('task_binding') or {}).get('node_id')==f'{goal_version}:place'
                  and (s.get('feedback') or {}).get('api')=='open_gripper'
                  and (s.get('feedback') or {}).get('category')=='insufficient_evidence'
                  and s.get('execution')=='failed']
        completed_rotations=[s for s in attempts if s.get('execution')=='completed' and (s.get('node_verification') or {}).get('status')=='verified']
        last_rotation=ordered.index(completed_rotations[-1]) if completed_rotations else -1
        needs_alternative=bool(releases and ordered.index(releases[-1])>last_rotation)
        state=('not_required' if not needs_alternative or released or complete else
               'blocked' if len(attempts)>=2 else 'ready' if all(actual(k)==v for k,v in requirements) else 'blocked')
        nodes.append(dict(id=identity,kind='reorient',title='在已验证高位尝试不同抓持朝向',status=state,
            depends_on=[f'{goal_version}:recover'],requirements=[dict(fact=k,expected=v,actual=actual(k)) for k,v in requirements],
            arguments=args,postconditions=[dict(fact='holding:'+bindings['object'],expected='verified')],
            skills=['reorient_held_object'],route='registered_skill',skill_only=True,last_attempt=None,
            reorientation_attempts=len(attempts),maximum_attempts=2,
            scope='Alternative orientation only; release evidence and final verification still required'))
    # Success-only continuations describe a registered completion option, not
    # permission to dispatch its future steps or a forecast of physical success.
    successors = {'clear_acquire': 'clear', 'clear': 'acquire',
                  'acquire': 'support' if name == 'stack_objects_v1' else None,
                  'support': 'place', 'reorient': 'place'}
    kinds = {n['kind'] for n in nodes}
    if 'reorient' in kinds:
        successors['recover'] = 'reorient'
    for node in nodes:
        kind = node['kind']
        node['purpose'] = 'restore_recorded_pose' if kind == 'recover' else 'goal_progress'
        if kind in {'place', 'verify'} or (kind == 'acquire' and name == 'hold_object_v1'):
            node['continuation'] = dict(goal_if_public_postcondition_verified=True)
        elif successors.get(kind) in kinds:
            node['continuation'] = dict(next_node=f'{goal_version}:{successors[kind]}')
        else:
            node['continuation'] = dict(unknown_reason='no_registered_success_continuation')
    return dict(name=name, version=1, authority='Runtime', bindings=copy.deepcopy(bindings),
                action_coverage='verification_only' if name == 'verify_existing_goal_v1' else 'bounded_cube_recipe'), nodes
