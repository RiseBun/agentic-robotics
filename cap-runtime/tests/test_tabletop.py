"""General tabletop protocol tests. Fixtures do not establish physical ability."""
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from agentic_cap.core import Manager, StaleRevision
from agentic_cap.tabletop_domain import validate_goal, validate_plan, compile_skill
from agentic_cap.tabletop_scene import public_catalog
from agentic_cap.tabletop_fixture import TabletopFixtureBackend


def until(fn,timeout=5):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if fn():return
        time.sleep(.01)
    raise AssertionError('Timed out')


def goal(*cs):return dict(all=list(cs))
def holding(obj='block_a'):return dict(predicate='holding',object=obj)
def on(obj='block_a',support='platform'):return dict(predicate='on',object=obj,support=support)
def node(ident,name,args,deps=None):return dict(id=ident,skill=dict(name=name,arguments=args),depends_on=deps or [])


class DomainTests(unittest.TestCase):
    def test_objects_and_relations_are_catalog_driven(self):
        cat=public_catalog();cat['objects']['unseen_object']=dict(movable=True)
        self.assertEqual(validate_goal(goal(holding('unseen_object')),cat)['all'][0]['object'],'unseen_object')
    def test_reject_unknown_predicate_and_privileged_fields(self):
        for c in [dict(predicate='teleport',object='block_a'),dict(predicate='holding',object='block_a',verified=True)]:
            with self.assertRaises(ValueError):validate_goal(goal(c),public_catalog())
    def test_reject_conflicting_and_cyclic_goals(self):
        for g in [goal(holding(),dict(predicate='empty_gripper')),goal(on('block_a','block_b'),on('block_b','block_a')),goal(holding(),on())]:
            with self.assertRaises(ValueError):validate_goal(g,public_catalog())
    def test_plan_does_not_accept_model_authored_conditions(self):
        n=node('pick','acquire_object',dict(object='block_a'));n['requires']=[]
        with self.assertRaises(ValueError):validate_plan(dict(nodes=[n]),public_catalog())
    def test_reject_dependency_cycle(self):
        ns=[node('a','inspect_object',dict(object='block_a'),['b']),node('b','inspect_object',dict(object='block_b'),['a'])]
        with self.assertRaises(ValueError):validate_plan(dict(nodes=ns),public_catalog())
    def test_compiled_skill_uses_provider_plan_ids(self):
        code,contract=compile_skill(dict(name='place_in_region',arguments=dict(object='block_b',region='left')),public_catalog())
        self.assertIn('plan_place',code);self.assertNotIn('goto_pose',code)
        self.assertEqual(contract['postcondition']['all'][0]['predicate'],'in_region')


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.manager=Manager(self.temp.name,TabletopFixtureBackend)
        s=self.manager.create('generic',seed=201,goal=goal(on(),dict(predicate='empty_gripper')),scene='tabletop_v1')
        self.e=self.manager.episodes[s['id']];until(lambda:self.e.snapshot and self.e.status=='ready')
    def tearDown(self):self.e.close();self.temp.cleanup()
    def submit(self,nodes,rid='plan'):
        return self.e.submit_plan(rid,self.e.revision,dict(nodes=nodes))
    def system(self):return self.e.observe()['system']
    def execute(self,ident,rid=None,system=None):
        s=system or self.system();n=next(n for n in s['task']['nodes'] if n['plan_node']==ident)
        skill=dict(name=n['skills'][0],arguments=n['arguments'])
        sid=self.e.execute_skill(rid or ident,self.e.revision,s['state_id'],n['id'],skill)
        until(lambda:self.e.active is None)
        return self.e.segments[sid]
    def test_default_budget_and_scene_goal_separation(self):
        self.assertEqual(self.e.limits,dict(decisions=24,calls=80,motion_steps=3000,wall_seconds=600))
        self.assertEqual(self.e.task,'tabletop');self.assertEqual(self.system()['task']['nodes'],[])
        with self.assertRaises(ValueError):self.manager.create('conflict',task='cube_stack',scene='tabletop_v1',goal=goal(holding()))
    def test_dynamic_multi_stage_plan_and_single_environment(self):
        self.submit([node('pick','acquire_object',dict(object='block_a')),node('place','place_object',dict(object='block_a',support='platform'),['pick'])])
        a=self.execute('pick');self.assertEqual(a['node_verification']['status'],'verified')
        b=self.execute('place');self.assertEqual(b['goal_verification']['status'],'verified')
        self.assertEqual(self.e.backend.reset_count,1);self.assertEqual(self.e.used['calls'],13)
        self.assertEqual(self.e.finish(self.e.revision)['status'],'verified')
    def test_unknown_blocks_manipulation(self):
        self.e.backend.occluded=True
        self.submit([node('pick','acquire_object',dict(object='block_a'))])
        with self.assertRaises(ValueError):self.execute('pick')
        self.assertEqual(self.e.used['calls'],0)
    def test_active_evidence_can_recover_then_pick(self):
        self.e.backend.occluded=True
        self.submit([node('look','inspect_object',dict(object='block_a')),node('pick','acquire_object',dict(object='block_a'),['look'])])
        self.assertEqual(self.execute('look')['execution'],'completed')
        self.assertEqual(self.e.backend.views,1)
        self.assertEqual(self.execute('pick')['stage_verification']['status'],'verified')
    def test_persistent_unknown_does_not_authorize_place(self):
        self.e.backend.occluded=True;self.e.backend.restore_after_view=False
        self.submit([node('look','inspect_object',dict(object='platform'))])
        self.execute('look');self.assertEqual(self.e.backend.views,2)
        self.assertEqual(self.e.finish(self.e.revision)['status'],'unknown')
    def test_plan_and_execution_idempotency(self):
        nodes=[node('pick','acquire_object',dict(object='block_a'))]
        a=self.submit(nodes);self.assertEqual(a,self.submit(nodes));first=self.execute('pick','once')
        s=self.system();n=s['task']['nodes'][0]
        # Repeat the exact original payload, not a new state reference.
        old=next(v for v in self.e.robot_system.cuts.values() if v['task']['nodes'][0]['status']=='ready')
        sid2=self.e.execute_skill('once',1,old['state_id'],old['task']['nodes'][0]['id'],dict(name='acquire_object',arguments=dict(object='block_a')))
        self.assertEqual(first['id'],sid2);self.assertEqual(len(self.e.segments),1)
    def test_goal_change_invalidates_plan_and_late_reply(self):
        self.submit([node('pick','acquire_object',dict(object='block_a'))]);old=self.system()
        self.e.update_goal(goal(holding('block_b')))
        with self.assertRaises(StaleRevision):self.e.execute_skill('late',old['revision'],old['state_id'],old['task']['nodes'][0]['id'],dict(name='acquire_object',arguments=dict(object='block_a')))
        self.assertEqual(self.system()['task']['nodes'],[])
    def test_unrelated_object_change_does_not_invalidate_binding(self):
        self.submit([node('pick','acquire_object',dict(object='block_a'))]);old=self.system()
        self.e.backend.versions['block_b']+=1;time.sleep(.25)
        self.assertEqual(self.execute('pick',system=old)['execution'],'completed')
    def test_relevant_object_change_invalidates_binding(self):
        self.submit([node('pick','acquire_object',dict(object='block_a'))]);old=self.system()
        self.e.backend.versions['block_a']+=1;time.sleep(.25)
        with self.assertRaises(ValueError):self.execute('pick',system=old)
    def test_crashed_generic_task_is_interrupted_and_never_replayed(self):
        self.submit([node('pick','acquire_object',dict(object='block_a'))])
        state=self.e.state();state['active_segment']='inflight'
        state['segments']['inflight']=dict(id='inflight',execution='running')
        with tempfile.TemporaryDirectory() as other:
            folder=Path(other)/self.e.id;folder.mkdir()
            (folder/'episode.json').write_text(json.dumps(state))
            restarted=Manager(other,TabletopFixtureBackend);record=restarted.get(self.e.id)
            self.assertEqual(record['status'],'interrupted');self.assertIsNone(record['active_segment'])
            self.assertEqual(record['segments']['inflight']['execution'],'cancelled')
            self.assertEqual(record['system']['contract_version'],2)
            self.assertFalse(record['system']['world']['fresh']);self.assertEqual(record['system']['task']['execution']['ready_nodes'],[])
            self.assertEqual(restarted.episodes,{})

    def test_restart_never_replays_generic_task(self):
        self.e.close();m=Manager(self.temp.name,TabletopFixtureBackend)
        self.assertEqual(m.get(self.e.id)['status'],'closed');self.assertEqual(m.episodes,{})
    def test_refused_release_can_restore_recorded_path_and_replan_without_reset(self):
        self.submit([node('pick','acquire_object',dict(object='block_a')),node('place','place_object',dict(object='block_a',support='platform'),['pick'])])
        self.execute('pick');self.e.backend.refuse_release=True
        refused=self.execute('place');self.assertEqual(refused['execution'],'failed')
        self.assertIsNotNone(self.e.backend.held)
        self.submit([node('restore','restore_grip_pose',dict(object='block_a')),node('place','place_object',dict(object='block_a',support='platform'),['restore'])],rid='repair')
        recovered=self.execute('restore');self.assertEqual(recovered['stage_verification']['status'],'verified')
        self.assertEqual(self.execute('place',rid='place-again')['goal_verification']['status'],'verified')
        self.assertEqual(self.e.backend.reset_count,1)

    def test_owner_confirmed_cancel_rejects_late_old_http_payloads(self):
        self.submit([node('pick','acquire_object',dict(object='block_a'))]);old=self.system();revision=self.e.revision
        self.assertTrue(self.e.cancel()['stopped']);self.assertGreater(self.e.revision,revision)
        with self.assertRaises(StaleRevision):
            self.e.execute_skill('late-after-stop',revision,old['state_id'],old['task']['nodes'][0]['id'],dict(name='acquire_object',arguments=dict(object='block_a')))
        self.assertEqual(self.e.used['calls'],0);self.assertEqual(self.e.backend.reset_count,1)

    def test_cancel_acknowledges_hold_and_stops_late_calls(self):
        self.submit([node('look','inspect_object',dict(object='block_a'))])
        self.e.backend.occluded=True;self.e.backend.restore_after_view=False
        s=self.system();n=s['task']['nodes'][0]
        sid=self.e.execute_skill('stop-view',self.e.revision,s['state_id'],n['id'],dict(name='inspect_object',arguments=dict(object='block_a')))
        until(lambda:self.e.used['calls']>0)
        result=self.e.cancel();self.assertTrue(result['stopped']);until(lambda:self.e.active is None)
        calls=self.e.used['calls'];time.sleep(.15)
        self.assertEqual(calls,self.e.used['calls']);self.assertEqual(self.e.segments[sid]['execution'],'cancelled')
    def test_model_wait_does_not_pause_owner(self):
        before=self.e.observe();idle_before=self.e.used['idle_steps']
        # Capture is throttled to 0.2s: sleeping exactly one capture interval
        # can legitimately return the previous published frame on a busy host.
        # This protocol gate checks bounded progress without model/API actions;
        # the separate physical delay probe measures real-time factor.
        until(lambda:self.e.observe()['simulation_time']-before['simulation_time']>.12)
        after=self.e.observe()
        self.assertGreater(after['captured_at'],before['captured_at'])
        self.assertGreater(after['simulation_time']-before['simulation_time'],.12)
        self.assertGreater(self.e.used['idle_steps'],idle_before)
        self.assertEqual(self.e.used['calls'],0)
        self.assertEqual(self.e.used['motion_steps'],0)
    def test_direct_cap_has_no_model_task_graph_and_cannot_use_foreign_operation(self):
        self.e.close()
        s=self.manager.create('direct',scene='tabletop_v1',seed=201,goal=goal(holding()),policy='direct_cap')
        self.e=self.manager.episodes[s['id']];until(lambda:self.e.snapshot and self.e.status=='ready')
        skill=dict(name='acquire_object',arguments=dict(object='block_a'))
        with self.assertRaises(ValueError):self.e.submit_plan('p',1,dict(nodes=[node('a','acquire_object',dict(object='block_a'))]))
        envelope=self.e.submit_operation('o',1,skill);self.assertEqual(envelope,self.e.submit_operation('o',1,skill))
        ref=envelope['task_ref']
        sid=self.e.execute('code',1,'direct','p=plan_grasp("block_a")\nexecute_stage(p,"approach")\nexecute_stage(p,"grasp")\nset_gripper("closed")\nexecute_stage(p,"lift")\nverify_effect({"all":[{"predicate":"holding","object":"block_a"}]})',task_ref=ref)
        until(lambda:self.e.active is None)
        self.assertEqual(self.e.segments[sid]['execution'],'completed')
    def test_runtime_budget_expiry_closes_without_a_connected_client(self):
        self.e.close()
        s=self.manager.create('expiry',scene='tabletop_v1',seed=201,goal=goal(holding()),limits=dict(wall_seconds=.15))
        self.e=self.manager.episodes[s['id']];until(lambda:self.e.status=='closed')
        self.assertEqual(self.e.termination_reason,'wall_budget_exhausted')
        self.assertGreaterEqual(self.e.hold_ack,self.e.hold_serial)

    def test_region_goal_uses_registered_verifier(self):
        self.e.update_goal(goal(dict(predicate='in_region',object='block_b',region='left'),dict(predicate='empty_gripper')))
        self.submit([node('pick','acquire_object',dict(object='block_b')),node('drop','place_in_region',dict(object='block_b',region='left'),['pick'])])
        self.execute('pick');self.assertEqual(self.execute('drop')['goal_verification']['status'],'verified')

if __name__=='__main__':unittest.main()
