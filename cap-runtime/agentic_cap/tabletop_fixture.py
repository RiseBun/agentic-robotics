"""Protocol fixture ONLY. Synthetic outcomes are never physical evaluation."""
import time
from .fixture import FixtureBackend
from .tabletop_domain import digest
from .tabletop_scene import public_catalog


class TabletopFixtureBackend(FixtureBackend):
    def __init__(self,episode):
        super().__init__(episode);self.relations={};self.regions={};self.plans={};self.pending=None
        self.versions={k:0 for k in public_catalog()['objects']};self.restore_after_view=True;self.views=0
        self.missed=False;self.recorded_descent=None;self.restored=None;self.refuse_release=False

    def capability_health(self):return dict(motion_planner=dict(status='fixture_only',collision_aware=False))

    def capture(self):
        s=super().capture();s.update(captured_at=time.time(),control_step=self.ticks,
            evidence_ref=f'fixture-{self.ticks}',simulation_time=self.sim_time(),object_versions=self.versions.copy(),
            catalog=public_catalog(),fixture_only=True)
        return s

    def verify(self,goal,snapshot):
        results=[]
        for c in goal['all']:
            pred=c['predicate'];obj=c.get('object')
            if pred=='restored_pose':ok=self.restored==obj
            elif pred=='recorded_descent':ok=self.recorded_descent==obj
            elif pred=='region':ok=True
            elif self.occluded:results.append('unknown');continue
            elif pred in {'visible','geometry','accessible','support_geometry'}:ok=True
            elif pred=='holding':ok=self.held==obj
            elif pred=='on':ok=self.relations.get(obj)==c['support']
            elif pred=='in_region':ok=self.regions.get(obj)==c['region']
            elif pred=='empty_gripper':ok=self.held is None
            else:results.append('unknown');continue
            results.append('verified' if ok else 'failed')
        return dict(status='failed' if 'failed' in results else 'unknown' if 'unknown' in results else 'verified',
                    reason='synthetic_protocol_assertion_only')

    def tools(self,segment):
        wait=super().tools(segment)['wait_motion'];e=self.episode
        def plan_grasp(obj):
            if self.held is not None:raise ValueError('Gripper is occupied')
            if self.occluded:return dict(status='unknown',reason='missing_geometry')
            ident=digest(dict(segment=segment['id'],object=obj,kind='grasp'))
            self.plans[ident]=dict(kind='grasp',object=obj,revision=e.revision,stage=None)
            return ident
        def plan_place(obj,support=None,region=None):
            if self.held!=obj or self.occluded:return dict(status='unknown',reason='holding_or_destination_unknown')
            ident=digest(dict(segment=segment['id'],object=obj,support=support,region=region))
            self.plans[ident]=dict(kind='place',object=obj,support=support,region=region,revision=e.revision,stage=None)
            return ident
        def execute_stage(ident,stage):
            e.checkpoint(segment);p=self.plans[ident]
            if p['revision']!=e.revision:raise ValueError('Stale plan')
            allowed={'grasp':{'approach','grasp','lift'},'place':{'approach','place','retreat'}}
            if stage not in allowed[p['kind']]:raise ValueError('Invalid stage')
            wait(8);e.checkpoint(segment);p['stage']=stage;self.pending=p;self.restored=None
            if p['kind']=='place' and stage=='place':self.recorded_descent=p['object']
            return dict(status='verified',fixture_only=True)
        def gripper(command):
            if command not in {'open','closed'}:raise ValueError('Unknown gripper command')
            e.checkpoint(segment);wait(4);p=self.pending
            if command=='closed':
                if not p or p['kind']!='grasp' or p['stage']!='grasp':raise ValueError('No admitted grasp')
                if not self.missed:
                    self.held=p['object'];self.relations.pop(self.held,None);self.regions.pop(self.held,None)
            else:
                if p and p['kind']=='grasp' and self.held is None:return dict(status='verified',fixture_only=True)
                # plan_grasp is a pure proposal; initial empty opening is safe.
                if self.held is None and any(row['kind']=='grasp' for row in self.plans.values()):return dict(status='verified',fixture_only=True)
                if self.refuse_release:return dict(status='unknown',reason='fixture_release_evidence_missing')
                if not p or p['kind']!='place' or p['stage']!='place' or self.occluded:
                    return dict(status='unknown',reason='release_requires_current_destination_evidence')
                if p.get('support'):self.relations[self.held]=p['support']
                if p.get('region'):self.regions[self.held]=p['region']
                self.held=None;self.recorded_descent=None
            return dict(status='verified',fixture_only=True)
        def inspect(obj,evidence="geometry"):
            e.event('evidence_acquisition_started',object=obj,before_evidence_ref=e.snapshot.get('evidence_ref'))
            before=e.used.copy();start=time.monotonic();views=0
            while self.occluded and views<2:
                e.checkpoint(segment);views+=1;self.views+=1
                e.event('view_attempt',object=obj,view_index=views,fixture_only=True)
                wait(5)
                if self.restore_after_view:self.occluded=False
            result=dict(status='unknown' if self.occluded else 'available',views_used=views,
                        object=obj,evidence_ref=e.snapshot.get('evidence_ref'),fixture_only=True,
                        motion_steps=e.used['motion_steps']-before['motion_steps'],wall_seconds=time.monotonic()-start)
            e.event('evidence_acquisition_finished',**result);return result
        def restore(obj):
            e.checkpoint(segment)
            if self.recorded_descent!=obj or self.held!=obj:return dict(status='unknown',reason='fixture_recorded_path_missing')
            wait(8);self.recorded_descent=None;self.restored=obj;self.refuse_release=False;self.occluded=False
            return dict(status='verified',fixture_only=True)
        return dict(plan_grasp=plan_grasp,plan_place=plan_place,execute_stage=execute_stage,set_gripper=gripper,
                    inspect_object=inspect,restore_grip_pose=restore,verify_effect=lambda goal:self.verify(goal,e.observe()))
