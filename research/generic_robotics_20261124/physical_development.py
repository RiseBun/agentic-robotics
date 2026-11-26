import json,time
from agentic_cap.core import Manager
from agentic_cap.tabletop_backend import TabletopBackend
root="/home/li/projects/agentic-robotics/research/generic_robotics_20261124/physical-development"
m=Manager(root,lambda e:TabletopBackend(e,evaluation=True))
s=m.create("physical-dev-201-v7",scene="tabletop_v1",seed=201,goal={"all":[{"predicate":"holding","object":"block_a"}]})
e=m.episodes[s["id"]]
try:
 end=time.monotonic()+30
 while time.monotonic()<end:
  if e.status=="failed":raise RuntimeError(e.events[-1])
  if e.snapshot and e.observe()["system"]["world"]["facts"]["geometry:block_a"]["status"]=="verified":break
  time.sleep(.2)
 obs=e.observe(); print("OBS",json.dumps(dict(status=e.status,objects=obs.get("objects"),reference=e.backend.reference,facts={k:v["status"] for k,v in obs["system"]["world"]["facts"].items()})),flush=True)
 before=e.backend.sim_time();start=time.monotonic();time.sleep(5);after=e.backend.sim_time()
 print("CONTINUITY",json.dumps(dict(wall=time.monotonic()-start,simulation=after-before)),flush=True)
 e.submit_plan("pick-plan",e.revision,{"nodes":[{"id":"pick","skill":{"name":"acquire_object","arguments":{"object":"block_a"}},"depends_on":[]}]})
 sys=e.observe()["system"];node=sys["task"]["nodes"][0]
 sid=e.execute_skill("pick",e.revision,sys["state_id"],node["id"],{"name":"acquire_object","arguments":{"object":"block_a"}})
 deadline=time.monotonic()+150
 while e.active and time.monotonic()<deadline:time.sleep(.2)
 if e.active:e.cancel()
 print("SEGMENT",json.dumps(e.segments[sid]),flush=True)
finally:e.close()
