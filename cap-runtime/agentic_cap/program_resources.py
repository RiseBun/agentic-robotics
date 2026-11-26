"""API-call bounds for validated restricted programs; no code or robot execution.

Bounds concern a full interpreter path, not physical success, time or motion.
The original per-call, cancellation and wall/motion guards remain authoritative.
"""
import ast
import copy
from .interpreter import Interpreter

RELEASE_FOLLOWUP_CALLS = 2


class StageBudgetError(RuntimeError):
    code = 'INSUFFICIENT_STAGE_BUDGET'

    def __init__(self, resources):
        self.evidence = copy.deepcopy(resources)
        super().__init__('Remaining API budget cannot cover the bounded program path')


def program_calls(tree, public_names, *, final_placement=False):
    # Inspector wrappers charge their own call plus bounded nested public APIs.
    from .active_evidence import MAX_VIEWS
    nested = {'inspect_support': (1, 2+2*MAX_VIEWS),
              'inspect_placement': (1, 2+MAX_VIEWS)}
    names = set(public_names)

    def add(values):
        values = list(values)
        low = high = required = 0
        for value in values:
            required = max(required, high+value[2])
            low += value[0]
            high += value[1]
        return low, high, max(required, high)

    def expression(node):
        if isinstance(node, ast.Call):
            arguments = add(expression(arg) for arg in [*node.args, *(k.value for k in node.keywords)])
            low,high = nested.get(node.func.id, (1,1)) if node.func.id in names else (0,0)
            followup = RELEASE_FOLLOWUP_CALLS if final_placement and node.func.id == 'open_gripper' and node.func.id in names else 0
            return add([arguments,(low,high,high+followup)])
        if isinstance(node, ast.BoolOp):
            costs = [expression(v) for v in node.values]
            return costs[0][0], sum(c[1] for c in costs), add(costs)[2]
        if isinstance(node, ast.Compare):
            costs = [expression(v) for v in [node.left,*node.comparators]]
            return sum(c[0] for c in costs[:2]), sum(c[1] for c in costs), add(costs)[2]
        return add(expression(child) for child in ast.iter_child_nodes(node) if isinstance(child, ast.expr))

    def block(nodes):
        costs=[]
        for node in nodes:
            if isinstance(node, ast.If):
                test=expression(node.test)
                yes,no=block(node.body),block(node.orelse)
                branch=(yes if node.test.value else no) if isinstance(node.test,ast.Constant) else (min(yes[0],no[0]),max(yes[1],no[1]),max(yes[2],no[2]))
                costs.append(add([test,branch]))
            elif isinstance(node, ast.For):
                iterable=expression(node.iter)
                lo,hi=0,16
                if isinstance(node.iter,(ast.List,ast.Tuple)):
                    lo=hi=len(node.iter.elts)
                elif (isinstance(node.iter,ast.Call) and isinstance(node.iter.func,ast.Name)
                      and node.iter.func.id=='range' and 'range' not in names and len(node.iter.args)==1
                      and isinstance(node.iter.args[0],ast.Constant) and isinstance(node.iter.args[0].value,int)):
                    lo=hi=min(16,max(0,node.iter.args[0].value))
                body=block(node.body)
                costs.append(add([iterable,(lo*body[0],hi*body[1],(hi-1)*body[1]+body[2] if hi else 0),block(node.orelse)]))
            elif isinstance(node,ast.Assign):
                # Subscript indices are evaluated on assignment too.
                targets=[expression(t) for t in node.targets]
                costs.append(add([expression(node.value),*targets]))
            elif isinstance(node,ast.Expr):
                costs.append(expression(node.value))
        return add(costs)

    low,high,required=block(tree.body)
    return dict(minimum_calls=low,maximum_calls=high,required_calls=required,
                release_followup_headroom_calls=RELEASE_FOLLOWUP_CALLS if final_placement else 0,analysis='validated_restricted_program_paths',
                coverage='public_API_calls_including_bounded_inspector_children',
                motion_steps_required=None,wall_seconds_required=None,
                scope='API headroom only; physical success, cancellation, motion and wall time are not guaranteed.')


def assess_calls(estimate, remaining_calls):
    return dict(estimate,remaining_calls=remaining_calls,
                status='fits' if remaining_calls>=estimate['required_calls'] else 'insufficient',
                admission_policy='reserve_for_maximum_bounded_API_path_without_charging_unused_calls')


def project_resources(nodes, facts, snapshot, budget):
    """Model-visible registered route estimates; CaP uses its actual code later."""
    if budget is None:
        return None
    from .skills import compile_skill
    remaining={key:budget['limits'][key]-budget['used'][key] for key in budget['limits'] if key in budget['used']}
    public_names={'get_object_pose','goto_pose','open_gripper','close_gripper','get_execution_state',
                  'check_holding','inspect_support','inspect_placement','pick','place_on'}
    system=dict(world=dict(facts=facts),task=dict(nodes=nodes))
    for node in nodes:
        routes={}
        for name in node['skills']:
            try:
                code,_=compile_skill(dict(name=name,arguments=node['arguments']),system,snapshot)
                tree=Interpreter({key:None for key in public_names},lambda:None).validate(code)
                routes[name]=assess_calls(program_calls(tree,public_names,final_placement=bool(snapshot.get('goal',{}).get('on'))),remaining['calls'])
            except (KeyError,ValueError,TypeError,StopIteration):
                routes[name]=dict(status='unknown',reason='current_public_preconditions_do_not_compile_a_route')
        node['resources']=dict(skills=routes,cap_program='actual_program_preflight_required' if not node.get('skill_only') else 'unavailable')
    return dict(remaining=remaining,coverage='stage_API_headroom; not whole_task_feasibility',
                motion_time_prediction='unavailable; existing cumulative limits still apply')
