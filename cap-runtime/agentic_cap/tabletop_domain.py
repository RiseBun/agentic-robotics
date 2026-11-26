"""Object-independent tabletop contracts. No simulator or model access."""
import copy
import hashlib
import json
import re
import os

LIMITS = dict(decisions=24, calls=80, motion_steps=3000, wall_seconds=600)
ID = re.compile(r'^[a-zA-Z][a-zA-Z0-9_-]{0,63}$')
PREDICATES = {'holding': {'object'}, 'on': {'object', 'support'},
              'in_region': {'object', 'region'}, 'empty_gripper': set()}
# Conditions and effects belong to the registry, never to model-authored code.
SKILLS = {
    'inspect_object': dict(parameters=['object'], optional_parameters={'evidence':['visible','geometry','holding','support_geometry']}, requires=[], ensures=['{evidence}:{object}'],
                           effects=[dict(predicate='{evidence}', object='{object}')],
                           description='Acquire new public geometry; at most two planned view changes.'),
    'acquire_object': dict(parameters=['object'], requires=['empty_gripper', 'visible:{object}', 'geometry:{object}', 'accessible:{object}'],
                           ensures=['holding:{object}'], effects=[dict(predicate='holding', object='{object}')],
                           description='Plan a grasp, approach, close, lift and independently check holding.'),
    'place_object': dict(parameters=['object','support'], requires=['holding:{object}', 'visible:{support}', 'support_geometry:{support}'],
                        ensures=['on:{object}:{support}', 'empty_gripper'],
                        effects=[dict(predicate='on', object='{object}', support='{support}'), dict(predicate='empty_gripper')],
                        description='Plan supported placement, release, retreat, then verify effects.'),
    'restore_grip_pose': dict(parameters=['object'],requires=['recorded_descent:{object}'],ensures=['holding:{object}','restored_pose:{object}'],
        effects=[dict(predicate='holding',object='{object}')],description='Reverse a recorded completed descent, preserve gripper, and verify current custody. No release or new grasp.'),
    'replan_grip_pose': dict(parameters=['object'],requires=['holding:{object}','recorded_retreat_target:{object}'],
        ensures=['holding:{object}','restored_pose:{object}'],effects=[dict(predicate='holding',object='{object}')],
        description='Explicitly replan from current public geometry to the recorded pre-descent pose, preserve gripper, then verify custody. Available only when enabled; no old-path reuse.'),
    'place_in_region': dict(parameters=['object','region'], requires=['holding:{object}', 'region:{region}'],
                           ensures=['in_region:{object}:{region}', 'empty_gripper'],
                           effects=[dict(predicate='in_region', object='{object}', region='{region}'), dict(predicate='empty_gripper')],
                           description='Place within a calibrated tabletop region and check public evidence.'),
}


def enabled_skills():
    return {k:v for k,v in SKILLS.items() if k!='replan_grip_pose' or os.environ.get('TABLETOP_REPLANNED_RETREAT','0')=='1'}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def validate_condition(condition, catalog, *, internal=False):
    if not isinstance(condition,dict): raise ValueError('Condition must be an object')
    predicate=condition.get('predicate')
    fields=PREDICATES.get(predicate)
    if internal and predicate in {'visible','geometry','accessible','recorded_descent','recorded_retreat_target','restored_pose','support_geometry'}: fields={'object'}
    if fields is None or set(condition)!={'predicate'}|fields:
        raise ValueError('Unsupported predicate or condition fields')
    for key in fields:
        name=condition[key]
        collection=catalog['regions'] if key=='region' else catalog['objects']
        if not isinstance(name,str) or name not in collection: raise ValueError('Unknown '+key)
    if predicate=='holding' and not catalog['objects'][condition['object']].get('movable',False):
        raise ValueError('Object is not movable')
    if predicate=='on' and condition['object']==condition['support']: raise ValueError('Self-support is invalid')
    return copy.deepcopy(condition)


def validate_goal(goal,catalog):
    if not isinstance(goal,dict) or set(goal)!={'all'} or not isinstance(goal['all'],list) or not 1<=len(goal['all'])<=12:
        raise ValueError('Goal requires 1..12 conjunctive conditions')
    conditions=[validate_condition(c,catalog) for c in goal['all']]
    if len({digest(c) for c in conditions})!=len(conditions): raise ValueError('Duplicate condition')
    held=[c['object'] for c in conditions if c['predicate']=='holding']
    if len(held)>1 or (held and any(c['predicate']=='empty_gripper' for c in conditions)):
        raise ValueError('Conflicting single-gripper goal')
    locations={}
    for c in conditions:
        if c['predicate'] in {'on','in_region'}:
            if c['object'] in held: raise ValueError('Held object cannot simultaneously be placed')
            prior=locations.get(c['object'])
            if prior and prior['predicate']==c['predicate']: raise ValueError('Conflicting placement destinations')
            locations[c['object']]=c
    edges={c['object']:c['support'] for c in conditions if c['predicate']=='on'}
    for obj in edges:
        seen=set();cursor=obj
        while cursor in edges:
            if cursor in seen: raise ValueError('Cyclic support goal')
            seen.add(cursor);cursor=edges[cursor]
    return {'all':conditions}


def fact_key(condition):
    p=condition['predicate']
    return ':'.join([p]+[condition[k] for k in ('object','support','region') if k in condition])


def condition_from_key(key):
    parts=key.split(':');p=parts[0]
    fields={'holding':['object'],'on':['object','support'],'in_region':['object','region'],
            'empty_gripper':[],'visible':['object'],'geometry':['object'],'accessible':['object'],'recorded_descent':['object'],'recorded_retreat_target':['object'],'restored_pose':['object'],'support_geometry':['object'],'region':['region']}[p]
    return dict(predicate=p,**dict(zip(fields,parts[1:])))


def validate_skill(skill,catalog):
    if not isinstance(skill,dict) or set(skill)!={'name','arguments'} or skill['name'] not in enabled_skills():
        raise ValueError('Unknown registered tabletop skill')
    name=skill['name'];args=skill['arguments'];spec=SKILLS[name]
    optional=spec.get('optional_parameters',{})
    if not isinstance(args,dict) or not set(spec['parameters'])<=set(args) or set(args)-set(spec['parameters'])-set(optional):raise ValueError('Invalid skill parameters')
    args=copy.deepcopy(args)
    for key,choices in optional.items():
        args.setdefault(key,'geometry')
        if args[key] not in choices:raise ValueError('Unsupported evidence target')
    for key,value in args.items():
        if key in optional:continue
        collection=catalog['regions'] if key=='region' else catalog['objects']
        if not isinstance(value,str) or value not in collection: raise ValueError('Unknown skill binding')
    if (name.startswith(('acquire','place')) or name=='replan_grip_pose') and not catalog['objects'][args['object']].get('movable',False):
        raise ValueError('Cannot manipulate a fixed support')
    if args.get('object')==args.get('support'): raise ValueError('Self-support is invalid')
    if name=='place_in_region' and not catalog['objects'][args['object']].get('movable',False):raise ValueError('Region placement requires a movable object')
    return name,copy.deepcopy(args)


def effects_for(name,args):
    return [{k:(v.format(**args) if isinstance(v,str) else v) for k,v in c.items()} for c in SKILLS[name]['effects']]


def validate_plan(plan,catalog):
    if not isinstance(plan,dict) or set(plan)!={'nodes'} or not isinstance(plan['nodes'],list) or not 1<=len(plan['nodes'])<=24:
        raise ValueError('Plan requires 1..24 nodes')
    result=[];ids=set()
    for row in plan['nodes']:
        if not isinstance(row,dict) or set(row)-{'id','skill','depends_on','title'} or not {'id','skill'}<=set(row):
            raise ValueError('Plan nodes accept id, skill, depends_on, title only; conditions are Runtime-owned')
        ident=row['id']
        if not isinstance(ident,str) or not ID.fullmatch(ident) or ident in ids: raise ValueError('Invalid or duplicate node ID')
        name,args=validate_skill(row['skill'],catalog)
        deps=row.get('depends_on',[])
        if not isinstance(deps,list) or len(deps)!=len(set(deps)) or any(not isinstance(d,str) for d in deps): raise ValueError('Invalid dependency list')
        title=row.get('title',name)
        if not isinstance(title,str) or len(title)>500: raise ValueError('Invalid node title')
        result.append(dict(id=ident,title=title,skill=dict(name=name,arguments=args),depends_on=deps))
        ids.add(ident)
    graph={n['id']:n['depends_on'] for n in result};visiting=set();visited=set()
    def visit(n):
        if n not in graph or n in visiting: raise ValueError('Unknown or cyclic plan dependency')
        if n in visited:return
        visiting.add(n)
        for dep in graph[n]:visit(dep)
        visiting.remove(n);visited.add(n)
    for n in graph:visit(n)
    return {'nodes':result}


def compile_skill(skill,catalog):
    name,a=validate_skill(skill,catalog)
    if name=='inspect_object':return f'inspect_object({a["object"]!r},evidence={a["evidence"]!r})',None
    if name in {'restore_grip_pose','replan_grip_pose'}:return f'{name}({a["object"]!r})',dict(postcondition={'all':effects_for(name,a)})
    plan='plan_grasp' if name=='acquire_object' else 'plan_place'
    arguments=repr(a['object'])
    if name=='place_object':arguments+=', support='+repr(a['support'])
    if name=='place_in_region':arguments+=', region='+repr(a['region'])
    # Plan IDs reference provider-produced geometry; no model-authored Cartesian targets.
    code=f'p={plan}({arguments})\n'
    if name=='acquire_object':
        code+='set_gripper("open")\nexecute_stage(p,"approach")\nexecute_stage(p,"grasp")\nset_gripper("closed")\nexecute_stage(p,"lift")\n'
    else:
        code+='execute_stage(p,"approach")\nexecute_stage(p,"place")\nset_gripper("open")\nexecute_stage(p,"retreat")\n'
    code+='verify_effect('+repr({'all':effects_for(name,a)})+')'
    return code,dict(postcondition={'all':effects_for(name,a)})
