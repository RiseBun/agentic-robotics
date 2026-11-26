import json,numpy as np,time
from cap_harness.contracts import Pose,RobotState,PlanningScene,PointCloud,RobotPlanningContext
from cap_harness.providers.curobo.client import CuRoboProvider
q=np.array([0.,-.5,0.,-2.2,0.,1.7,.785]);names=tuple(f"panda_joint{i}" for i in range(1,8))
state=RobotState(q,np.zeros(7),Pose(np.array([.4,0,.4]),np.array([1.,0,0,0]),"robot_base"),1.,"robot_base",joint_names=names)
context=RobotPlanningContext("robosuite","panda",{"primary":names},{"primary":np.eye(4)},{"primary":"panda_hand"})
points=np.array([[x,y,-.05] for x in np.linspace(-.2,.8,20) for y in np.linspace(-.5,.5,20)])
p=CuRoboProvider(timeout_s=90,max_retries=0);t=time.time()
result=p.plan_to_joints(state,q+np.array([.05,0,0,0,0,0,0]),scene=PlanningScene(PointCloud(points,"robot_base")),context=context)
print(json.dumps(dict(ok=result.ok,elapsed=time.time()-t,error=None if result.error is None else str(result.error),trajectory_steps=None if result.trajectory is None else len(result.trajectory.joint_positions),collision_aware=None if result.trajectory is None else result.trajectory.collision_aware),default=str))
