"""Evidence contract tests use only public packets, never simulator truth."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from agentic_cap.tabletop_evidence import stamp_packet,source_for,save_camera,geometry_problem
from agentic_cap.tabletop_backend import TabletopBackend
from agentic_cap.tabletop_sensors import verify
import test_tabletop_sensors as sensor_fixtures

class ProvenanceTests(unittest.TestCase):
    def packet(self,step=20,at=100.):
        camera=dict(rgb=np.zeros((8,8,3),dtype=np.uint8),depth=np.ones((8,8)),
                    intrinsics=np.eye(3),camera_to_world=np.eye(4),captured_at=at-.01)
        return stamp_packet(dict(cameras={'overview':copy.deepcopy(camera),'wrist':copy.deepcopy(camera)},
            control_step=step,captured_at=at,joint_positions=[0.]*7,tcp_xyz=[0,0,1.],tcp_wxyz=[1,0,0,0],
            gripper_joint_positions=[.04,.04],last_gripper_command='open'),'episode')
    def fixture(self):
        s,h,r=sensor_fixtures.EvidenceTests().data()
        for row in [*h,s]:
            packet=self.packet(row['control_step'],row['captured_at'])
            row.update(episode_id='episode',geometry_evidence_schema=1,geometry_not_before_step=0,
                geometry_robot_state={k:packet[k] for k in ('control_step','captured_at','tcp_xyz','tcp_wxyz')})
            for obj in row['objects'].values():
                obj.update(camera='overview',evidence_source=source_for(packet,['overview']))
        return s,h,r
    def goal(self):return {'all':[dict(predicate='on',object='block_a',support='platform')]}
    def status(self,s,h,r):return verify(self.goal(),s,h,r)['status']
    def test_both_camera_artifacts_record_their_capture_robot_state(self):
        packet=self.packet()
        with tempfile.TemporaryDirectory() as folder:
            for camera in ('overview','wrist'):
                save_camera(folder,packet,camera)
                with np.load(Path(folder)/packet['cameras'][camera]['evidence_ref'],allow_pickle=False) as data:
                    self.assertEqual(data['control_step'],20)
                    self.assertEqual(data['captured_at'],100.)
                    self.assertEqual(data['camera_captured_at'],99.99)
                    self.assertEqual(str(data['episode_id']),'episode')
                    np.testing.assert_equal(data['tcp_xyz'],[0,0,1.])
                    np.testing.assert_equal(data['joint_positions'],[0.]*7)
                    self.assertNotIn('objects',data.files)
    def test_source_ref_remains_at_the_measurement_frame_after_new_camera_capture(self):
        old=self.packet(20,100.);source=source_for(old,['wrist'])
        new=self.packet(40,100.5)
        self.assertEqual(source['observations'][0]['evidence_ref'],'view-robot0_eye_in_hand-000020.npz')
        self.assertEqual(new['cameras']['wrist']['evidence_ref'],'view-robot0_eye_in_hand-000040.npz')
    def test_valid_versioned_independent_evidence_still_verifies(self):
        s,h,r=self.fixture();self.assertEqual(self.status(s,h,r),'verified')
    def test_missing_wrong_episode_frame_or_pairing_fails_closed(self):
        for change in ('missing','episode','frame','pose','camera','future','nan'):
            with self.subTest(change=change):
                s,h,r=self.fixture();o=s['objects']['block_a'];src=o['evidence_source']
                if change=='missing':o.pop('evidence_source')
                elif change=='episode':src['episode_id']='other'
                elif change=='frame':src['frame']='robot_base'
                elif change=='pose':s['geometry_robot_state']['control_step']+=1
                elif change=='camera':src['observations'][0]['camera']='wrist'
                elif change=='future':src['captured_at']=s['captured_at']+.1
                elif change=='nan':src['captured_at']=float('nan')
                self.assertIsNotNone(geometry_problem(o,s))
                self.assertEqual(self.status(s,h,r),'unknown')
    def test_asynchronous_fresh_capture_does_not_refresh_old_measurement(self):
        s,h,r=self.fixture();s['objects']['block_a']['evidence_source']['captured_at']-=2
        self.assertEqual(self.status(s,h,r),'unknown')
    def test_camera_time_disagreement_stays_unknown(self):
        s,h,r=self.fixture();s['objects']['platform']['evidence_source']['observations'][0]['captured_at']-=.2
        self.assertEqual(self.status(s,h,r),'unknown')
    def test_repeated_geometry_under_fresh_camera_packets_cannot_verify_placement(self):
        s,h,r=self.fixture()
        for row in h:
            row['geometry_robot_state']=copy.deepcopy(h[0]['geometry_robot_state'])
            row['objects']=copy.deepcopy(h[0]['objects'])
        self.assertEqual(self.status(s,h,r),'unknown')
    def test_distinct_measurements_must_span_existing_window(self):
        s,h,r=self.fixture()
        for i,row in enumerate(h):
            t=s['captured_at']-.1+i*.02
            row['geometry_robot_state']['captured_at']=t
            for o in row['objects'].values():
                o['captured_at']=t;o['evidence_source']['captured_at']=t
                for cam in o['evidence_source']['observations']:cam['captured_at']=t-.01
        self.assertEqual(self.status(s,h,r),'unknown')
    def test_pre_action_geometry_cannot_supply_post_action_stability(self):
        s,h,r=self.fixture()
        s['geometry_not_before_step']=3
        self.assertEqual(self.status(s,h,r),'unknown')
    def test_repeated_support_frame_cannot_verify_stable_relation(self):
        s,h,r=sensor_fixtures.EvidenceTests().data()
        for row in h:row['objects']['platform']['evidence_source']={'control_step':0,'captured_at':h[0]['captured_at']}
        self.assertEqual(self.status(s,h,r),'unknown')
    def test_planning_uses_worker_capture_pose_not_an_older_ui_snapshot(self):
        from types import SimpleNamespace
        packet=self.packet(step=20,at=100.)
        obj=dict(status='available',captured_at=99.99,camera='overview',evidence_source=source_for(packet,['overview']))
        b=object.__new__(TabletopBackend);b.episode=SimpleNamespace(id='episode')
        b.segmented_packet=(packet,{'block_a':obj});b.tick=22;b.last_action_tick=10
        with patch('agentic_cap.tabletop_backend.time.time',return_value=100.1):
            self.assertIs(b._geometry_packet('block_a')[0],packet)
            b.last_action_tick=21
            with self.assertRaises(ValueError):b._geometry_packet('block_a')
            b.last_action_tick=10
        with patch('agentic_cap.tabletop_backend.time.time',return_value=102.):
            with self.assertRaises(ValueError):b._geometry_packet('block_a')

    def test_action_waits_for_new_capture_and_keeps_cancellation(self):
        import threading,time
        from types import SimpleNamespace
        from agentic_cap.core import Halt
        b=object.__new__(TabletopBackend);events=[];cancel=threading.Event()
        def checkpoint(_segment):
            if cancel.is_set():raise Halt('cancelled','CANCELLED')
        b.episode=SimpleNamespace(id='episode',cv=threading.Condition(),checkpoint=checkpoint,event=lambda kind,**kw:events.append((kind,kw)))
        b.tick=40;b.last_action_tick=21
        def install(step):
            packet=self.packet(step,time.time())
            obj=dict(status='available',captured_at=packet['captured_at']-.01,camera='overview',evidence_source=source_for(packet,['overview']))
            b.segmented_packet=(packet,{'block_a':obj})
        install(20)
        def publish():
            time.sleep(.06);install(30)
        worker=threading.Thread(target=publish);worker.start()
        try:self.assertEqual(b._geometry_packet('block_a',{},timeout=.5)[0]['control_step'],30)
        finally:worker.join()
        self.assertEqual([name for name,_ in events],['geometry_wait_started','geometry_wait_finished'])
        install(20)
        with self.assertRaises(ValueError):b._geometry_packet('block_a',{},timeout=.02)
        cancel.set()
        with self.assertRaises(Halt):b._geometry_packet('block_a',{},timeout=.5)

    def test_effect_wait_requires_proof_and_remains_bounded_and_cancellable(self):
        import threading
        from types import SimpleNamespace
        from agentic_cap.core import Halt
        b=object.__new__(TabletopBackend);events=[];cancel=False
        def checkpoint(_segment):
            if cancel:raise Halt('cancelled','CANCELLED')
        b.episode=SimpleNamespace(cv=threading.Condition(),checkpoint=checkpoint,
            event=lambda kind,**kw:events.append(kind),observe=lambda:{})
        results=iter([{'status':'unknown'},{'status':'verified'}])
        b.verify=lambda *_:next(results)
        self.assertEqual(b.wait_public_effect({}, {},timeout=.2)['status'],'verified')
        self.assertEqual(events,['public_effect_wait_started','public_effect_wait_finished'])
        b.verify=lambda *_:{'status':'unknown'}
        self.assertEqual(b.wait_public_effect({}, {},timeout=.01)['status'],'unknown')
        b.verify=lambda *_:{'status':'failed'}
        self.assertEqual(b.wait_public_effect({}, {},timeout=.2)['status'],'failed')
        cancel=True
        with self.assertRaises(Halt):b.wait_public_effect({}, {},timeout=.2)

    def test_readiness_requires_independent_post_action_measurements(self):
        b=object.__new__(TabletopBackend);b.last_action_tick=10
        b.history=[dict(control_step=i,captured_at=100+i/10,geometry_robot_state=dict(control_step=10,captured_at=100.)) for i in range(10,20)]
        self.assertEqual(b.verification_readiness([],{} )['status'],'pending')
        b.history=[dict(geometry_robot_state=dict(control_step=i,captured_at=100+t)) for i,t in [(10,0),(15,.3),(20,.7)]]
        self.assertEqual(b.verification_readiness([],{})['status'],'ready')
        b.last_action_tick=21
        self.assertEqual(b.verification_readiness([],{})['status'],'pending')

if __name__=='__main__':unittest.main()
