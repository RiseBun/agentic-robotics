import tempfile,time,json
from agentic_cap.core import Manager
from agentic_cap.tabletop_backend import TabletopBackend
root="/home/li/projects/agentic-robotics/research/generic_robotics_20261124/scene-smoke"
m=Manager(root,lambda e:TabletopBackend(e,evaluation=True))
s=m.create("scene-smoke-v3",scene="tabletop_v1",seed=201,goal={"all":[{"predicate":"holding","object":"block_a"}]})
e=m.episodes[s["id"]]
try:
 end=time.monotonic()+45
 while time.monotonic()<end and e.status not in {"failed","closed"}:
  if e.snapshot:
   a=e.observe(); print(json.dumps(dict(status=e.status,time=a.get("simulation_time"),objects={k:v.get("status") for k,v in a.get("objects",{}).items()},error=a.get("perception_error"))),flush=True)
   if len(a.get("objects",{}))>=4:break
  time.sleep(2)
 if e.status=="failed":print(json.dumps(e.events[-2:]),flush=True)
finally:e.close()
