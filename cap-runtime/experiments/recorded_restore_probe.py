"""Development-only planning probe. Never execute its returned trajectory."""
from agentic_cap.core import Halt


def probe_retreat(backend,segment,plan):
    e=backend.episode;obj=plan['object'];e.checkpoint(segment)
    saved=backend.verify({'all':[dict(predicate='recorded_descent',object=obj)]},e.observe())
    custody=backend.verify({'all':[dict(predicate='holding',object=obj)]},e.observe())
    if saved.get('blockers')!=['scene_versions_match'] or custody.get('status')!='verified':
        result=dict(status='not_attempted',reason='requires_only_scene_version_blocker_and_current_custody',
            restore_admission=saved,custody=custody)
        e.event('restore_replan_probe_finished',**result);return result
    xyz,quat=plan['targets']['approach']
    e.event('restore_replan_probe_started',object=obj,target_xyz=list(map(float,xyz)),
        target_wxyz=list(map(float,quat)),restore_admission=saved,custody=custody,
        scope='planning_only; no trajectory_dispatch')
    try:
        trajectory=backend._plan_pose(segment,xyz,quat,exclude=obj)
        e.checkpoint(segment)
        result=dict(status='planned',object=obj,waypoints=len(trajectory.joint_positions),
            collision_aware=bool(trajectory.collision_aware),executed=False,
            scope='current_observed_scene_and_robot_only; attached_object_not_modeled')
    except Halt:raise
    except Exception as exc:
        result=dict(status='planning_failed',object=obj,error=str(exc),executed=False)
    e.event('restore_replan_probe_finished',**result);return result


def probed_backend(base):
    class ProbeBackend(base):
        def tools(self,segment):
            tools=super().tools(segment);original=tools['set_gripper']
            def gripper(command):
                try:return original(command)
                except ValueError:
                    plan=self.plans.get(self.last_plan,{})
                    if command=='open' and plan.get('kind')=='place' and not getattr(self,'restore_probe_done',False):
                        self.restore_probe_done=True;probe_retreat(self,segment,plan)
                    raise
            tools['set_gripper']=gripper;return tools
    return ProbeBackend
