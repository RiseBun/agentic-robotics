"""Task admission and compiled motion must consume the same public support view."""
import ast
import copy
import time
import unittest
from agentic_cap.robot_system import RobotSystem, TaskExecutive
from agentic_cap.skills import compile_skill, selected_support_geometry
from agentic_cap.placement_retry import placement_context


class SupportGeometrySourceTests(unittest.TestCase):
    def snapshot(self):
        geometry=dict(status='visible',surface_q05_xyz=[.4,.1,.02],surface_q95_xyz=[.44,.14,.06],surface_span_xyz=[.04,.04,.04])
        return dict(backend='cap',captured_at=time.time(),control_step=100,evidence_ref='frame-000100.npz',
            objects={'red cube':dict(status='visible',surface_span_xyz=[.04,.04,.04]),'green cube':dict(status='unknown')},
            support_evidence={'green cube':dict(status='available',camera_name='robot0_eye_in_hand',
                evidence_ref='view-robot0_eye_in_hand-000100.npz',support_geometry=geometry)},
            capabilities=['inspect_support','inspect_placement'])

    def system(self,snapshot):
        def verify(condition,observation):
            if condition==dict(holding='red cube'):
                return dict(status='verified',evidence=[dict(estimated_center_tcp_offset_xyz=[.01,0,0],reference_tcp_wxyz=[0,0,1,0])])
            if condition.get('on')==['green cube','red cube']:return dict(status='failed')
            return dict(status='unknown')
        return RobotSystem('episode').publish(snapshot,dict(on=['red cube','green cube'],empty_gripper=True),1,verify,
            status='ready',active=None,segments={})

    def test_wrist_geometry_admits_and_compiles_placement_when_primary_cannot_localize_support(self):
        snapshot=self.snapshot();system=self.system(snapshot)
        node=next(n for n in system['task']['nodes'] if n['kind']=='place')
        self.assertEqual(node['status'],'ready')
        TaskExecutive.admit(system,node)
        fact=system['world']['facts']['support_geometry:green cube']
        self.assertEqual(fact['result']['evidence_ref'],'view-robot0_eye_in_hand-000100.npz')
        code,_=compile_skill(dict(name='place_object',arguments=dict(object='red cube',support='green cube')),system,snapshot)
        first=ast.parse(code).body[0].value
        target=ast.literal_eval(first.args[0])
        for actual,expected in zip(target,[.41,.12,.20]):self.assertAlmostEqual(actual,expected)
        context=placement_context(snapshot,system['world']['facts'],dict(object='red cube',support='green cube'))
        self.assertEqual(context['support_min'],[.4,.1,.02])
        self.assertNotEqual(system['world']['facts']['goal']['status'],'verified')

    def test_unknown_secondary_cannot_supply_geometry_or_authorize_motion(self):
        snapshot=self.snapshot();snapshot['support_evidence']['green cube']['status']='unknown'
        system=self.system(snapshot);node=next(n for n in system['task']['nodes'] if n['kind']=='place')
        self.assertEqual(system['world']['facts']['support_geometry:green cube']['status'],'unknown')
        self.assertEqual(node['status'],'blocked')
        with self.assertRaises(ValueError):TaskExecutive.admit(system,node)
        self.assertIsNone(placement_context(snapshot,system['world']['facts'],dict(object='red cube',support='green cube')))

    def test_malformed_selected_bounds_block_even_when_primary_has_valid_geometry(self):
        for bounds in [[float('nan'),.1,.02],[.5,.2,.1]]:
            snapshot=self.snapshot();snapshot['objects']['green cube']=copy.deepcopy(snapshot['support_evidence']['green cube']['support_geometry'])
            snapshot['support_evidence']['green cube']['support_geometry']['surface_q05_xyz']=bounds
            system=self.system(snapshot)
            self.assertEqual(system['world']['facts']['support_geometry:green cube']['status'],'unknown')
            self.assertEqual(next(n for n in system['task']['nodes'] if n['kind']=='place')['status'],'blocked')

    def test_stale_bundle_invalidates_selected_geometry(self):
        snapshot=self.snapshot();snapshot['captured_at']-=2
        system=self.system(snapshot)
        self.assertFalse(system['world']['fresh'])
        self.assertEqual(system['world']['facts']['support_geometry:green cube']['status'],'unknown')

    def test_primary_only_behavior_retains_existing_geometry(self):
        snapshot=self.snapshot();e=snapshot['support_evidence']['green cube']
        snapshot['objects']['green cube']=e.pop('support_geometry');e.pop('camera_name');e['evidence_ref']=snapshot['evidence_ref']
        self.assertEqual(selected_support_geometry(snapshot,'green cube'),snapshot['objects']['green cube'])
        self.assertEqual(next(n for n in self.system(snapshot)['task']['nodes'] if n['kind']=='place')['status'],'ready')
