"""Registered, bounded compositions of the existing public CaP interface.

Neither model arguments nor code acquire backend/environment access. Generated
skill programs execute through the same interpreter, guards and budget ledger.
"""
import copy
import math


OBJECTS = {'red cube', 'green cube'}
DEFINITIONS = {
    'acquire_object': dict(parameters=['object'], nodes=['acquire', 'clear_acquire'],
                           requires=['empty_gripper'], sensor_requires=['visible:{object}'],
                           ensures=['holding:{object}'],
                           policy='bounded_cube_topdown_v1',
                           description='Localize a cube, top-down grasp, lift and verify in one cancellable skill.'),
    'reorient_held_object': dict(parameters=['object'], nodes=['reorient'],
        requires=['holding:{object}', 'rotation_clearance:{object}'], sensor_requires=['holding_geometry:{object}'],
        ensures=['holding:{object}'], description='Experimental: at most two high-clearance yaw alternatives after rejected release and retreat. Never release.'),
    'place_object': dict(parameters=['object', 'support'], nodes=['place'],
                        requires=['holding:{object}', 'support:{support}'],
                        sensor_requires=['holding_geometry:{object}', 'support_geometry:{support}', 'extent:{object}'],
                        ensures=['on:{object}:{support}', 'empty_gripper'],
                        description='Use public holding offset and support geometry, release and retreat.'),
    'inspect_support': dict(parameters=['object', 'support'], nodes=['support'],
                           requires=['holding:{object}'], sensor_requires=[],
                           state_requires=[dict(fact='retreat_path:{object}', expected='unknown')],
                           ensures=['holding:{object}', 'support:{support}'],
                           description='Bounded public support evidence acquisition while holding.'),
    'inspect_placement': dict(parameters=['object', 'support'], nodes=['verify'],
                             requires=['empty_gripper', 'release_recorded'], sensor_requires=[],
                             ensures=['on:{object}:{support}', 'empty_gripper'],
                             description='Bounded post-release evidence acquisition; unknown stays unknown.'),
    'retreat_from_descent': dict(parameters=['object'], nodes=['recover'],
                                requires=['retreat_path:{object}'], sensor_requires=[],
                                ensures=['holding:{object}'],
                                description='Reverse the recorded vertical descent, preserve grip, then verify holding.'),
}


def catalogue(snapshot):
    capabilities = snapshot.get('capabilities', [])
    return [dict(name=name, **copy.deepcopy(spec),
                 execution='restricted_program', effect='Runtime public postcondition; never code completion')
            for name, spec in DEFINITIONS.items()
            if name not in {'inspect_support', 'inspect_placement', 'reorient_held_object'} or name in capabilities]


def requirements_for(name, arguments, snapshot):
    """The task projection and dispatcher use the same registered predicates."""
    spec = DEFINITIONS[name]
    keys = spec['requires'] + ([] if snapshot.get('backend') == 'fixture' else spec['sensor_requires'])
    return ([(key.format(**arguments), 'verified') for key in keys]
            + [(item['fact'].format(**arguments), item['expected']) for item in spec.get('state_requires', [])])


def valid_vector(value, length):
    return (isinstance(value, list) and len(value) == length
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) for x in value))


def holding_geometry(result):
    """Only measured, current holding evidence can supply placement geometry."""
    evidence = result.get('evidence', [])
    for row in evidence if isinstance(evidence, list) else []:
        if not isinstance(row, dict):
            continue
        offset, quat = row.get('estimated_center_tcp_offset_xyz'), row.get('reference_tcp_wxyz')
        if valid_vector(offset, 3) and valid_vector(quat, 4) and sum(x*x for x in quat) >= 1e-16:
            return dict(offset_xyz=copy.deepcopy(offset), tcp_wxyz=copy.deepcopy(quat))
    return None


def selected_support_geometry(snapshot, name):
    """Use the same checked public view as support evidence, including its source.

    Secondary geometry is never accepted from an unknown evidence result.
    View freshness/calibration is checked by the sensor selector before publication.
    """
    evidence=snapshot.get('support_evidence', {}).get(name, {})
    if evidence.get('status') == 'available' and evidence.get('support_geometry') is not None:
        return evidence['support_geometry']
    return snapshot.get('objects', {}).get(name, {})


def validate_skill(skill):
    if not isinstance(skill, dict) or set(skill) != {'name', 'arguments'}:
        raise ValueError('Skill requires name and arguments only')
    name, args = skill['name'], skill['arguments']
    if not isinstance(name, str) or name not in DEFINITIONS:
        raise ValueError('Unknown registered skill')
    if not isinstance(args, dict) or set(args) != set(DEFINITIONS[name]['parameters']):
        raise ValueError('Skill arguments do not match the registered contract')
    if any(not isinstance(value, str) or value not in OBJECTS for value in args.values()):
        raise ValueError('Skill arguments require supported cube identities')
    if len(args) == 2 and args['object'] == args['support']:
        raise ValueError('Object and support must differ')
    return name, copy.deepcopy(args)


def vector(value, length):
    if not valid_vector(value, length):
        raise ValueError('Skill requires finite public geometry')
    return value


def rotation_clearance(snapshot, obj):
    """Bounded two-cube sensor envelope, not general collision certification."""
    if (snapshot.get('recovery_options', {}).get('vertical_retreat') or {}).get('status') == 'available':
        return False
    tcp, quat=snapshot.get('tcp_xyz'), snapshot.get('tcp_wxyz')
    span=snapshot.get('objects', {}).get(obj, {}).get('surface_span_xyz')
    if not valid_vector(tcp,3) or not valid_vector(quat,4) or not valid_vector(span,3):return False
    if not (.015 <= max(span) <= .08 and .2 <= tcp[0] <= .75 and abs(tcp[1]) <= .25):return False
    if sum(x*x for x in quat)<1e-8:return False
    for name in OBJECTS-{obj}:
        surface=selected_support_geometry(snapshot,name)
        hi=surface.get('surface_q95_xyz')
        if surface.get('status')!='visible' or not valid_vector(hi,3) or tcp[2]-max(span)<hi[2]+.08:return False
    return True


def compile_skill(skill, system, snapshot):
    name, args = validate_skill(skill)
    obj, support = args['object'], args.get('support')
    holding = dict(holding=obj)
    final = dict(on=[obj, support], empty_gripper=True) if support else holding
    # This adapter is explicitly confined to protocol fixtures, never reported
    # as robot control or evidence of physical competence.
    fixture = snapshot.get('backend') == 'fixture'
    if name == 'acquire_object':
        from .placement_evidence import empty_gripper_evidence
        already_open = empty_gripper_evidence(snapshot)['status'] == 'verified'
        opening = '' if already_open else 'open_gripper()\n'
        code = (f'pick({obj!r})' if fixture else
                f'p,unused,extent=get_object_pose({obj!r})\n' + opening +
                'goto_pose(p,[0.,0.,1.,0.],z_approach=0.10)\nclose_gripper()\n'
                's=get_execution_state()\np=s["tcp_xyz"]\np[2]=p[2]+0.12\n'
                f'goto_pose(p,s["tcp_wxyz"])\ncheck_holding({obj!r})')
        contract = dict(preconditions=[dict(empty_gripper=True)], postcondition=holding)
    elif name == 'place_object':
        contract = dict(preconditions=[holding], postcondition=final)
        if fixture:
            code = f'place_on({support!r})'
        else:
            fact = system['world']['facts'][f'holding:{obj}']
            hold = holding_geometry(fact['result'])
            if hold is None:
                raise ValueError('Placement requires a current public holding offset')
            offset, quat = hold['offset_xyz'], hold['tcp_wxyz']
            objects = snapshot.get('objects', {})
            top, base = objects.get(obj, {}), selected_support_geometry(snapshot, support)
            lo, hi = vector(base.get('surface_q05_xyz'), 3), vector(base.get('surface_q95_xyz'), 3)
            width = max(vector(top.get('surface_span_xyz'), 3))
            if not .015 <= width <= .08:
                raise ValueError('Held cube extent outside the bounded placement skill')
            target = [(lo[0]+hi[0])/2-offset[0], (lo[1]+hi[1])/2-offset[1],
                      hi[2]+width/2-offset[2]]
            above = [target[0], target[1], target[2]+.12]
            from .cartesian_motion import MOTION_PROTOCOL
            margin = 2*MOTION_PROTOCOL['position_tolerance_m']
            code = (f'goto_pose({above!r},{quat!r})\ns=get_execution_state()\np=s["tcp_xyz"]\n'
                    f'p[2]={target[2]!r}\ngoto_pose(p,s["tcp_wxyz"])\nopen_gripper()\n'
                    's=get_execution_state()\np=s["tcp_xyz"]\nz=p[2]+0.12\n'
                    'for name in ["red cube","green cube"]:\n'
                    '    cube=s["objects"][name]\n'
                    '    if cube["status"]=="visible":\n'
                    f'        z=max(z,cube["surface_q95_xyz"][2]+{.10+margin!r})\n'
                    'p[2]=z\ngoto_pose(p,s["tcp_wxyz"])')
    elif name == 'reorient_held_object':
        if not rotation_clearance(snapshot,obj):raise ValueError('Current public high-clearance envelope required')
        node=next(n for n in system['task']['nodes'] if n['kind']=='reorient')
        used=node.get('reorientation_attempts',2)
        if used not in (0,1):raise ValueError('Reorientation alternatives exhausted')
        # First +25 degrees, then -50 relative to the first alternative. Never
        # translate, release, or choose the same yaw twice within this goal.
        angle=math.radians(25 if used==0 else -50)/2
        q=vector(snapshot.get('tcp_wxyz'),4);length=math.sqrt(sum(v*v for v in q));w,x,y,z=[v/length for v in q]
        c,s=math.cos(angle),math.sin(angle)
        target=[c*w-s*z,c*x-s*y,c*y+s*x,c*z+s*w]
        code=f's=get_execution_state()\ngoto_pose(s["tcp_xyz"],{target!r})\ncheck_holding({obj!r})'
        contract=dict(preconditions=[holding],postcondition=holding)
    elif name == 'retreat_from_descent':
        route = system['world']['facts']['retreat_path:'+obj]['result']['route']
        target, quat = vector(route.get('target_xyz'), 3), vector(route.get('target_wxyz'), 4)
        code = f'goto_pose({target!r},{quat!r})\ncheck_holding({obj!r})'
        # Permission comes from the existing recorded-descent guard; current
        # holding at a low placement pose may be unknown and is checked after.
        contract = dict(preconditions=[], postcondition=holding)
    elif name == 'inspect_support':
        code = f'inspect_support({support!r},{obj!r})'
        contract = dict(preconditions=[holding], postcondition=holding)
    else:
        code = f'inspect_placement({obj!r},{support!r})'
        contract = dict(preconditions=[dict(empty_gripper=True)], postcondition=final)
    return code, contract
