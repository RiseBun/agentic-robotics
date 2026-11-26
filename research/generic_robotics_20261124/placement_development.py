import json,time,sys
from agentic_cap.core import Manager
from agentic_cap.tabletop_backend import TabletopBackend
from agentic_cap.tabletop_evaluation import score_closed_batch
root="/home/li/projects/agentic-robotics/research/generic_robotics_20261124/placement-development"
goal={"all":[{"predicate":"on","object":"block_a","support":"platform"},{"predicate":"empty_gripper"}]}
m=Manager(root,lambda e:TabletopBackend(e,evaluation=True))
s=m.create("place-dev-201-v6",scene="tabletop_v1",seed=201,goal=goal);e=m.episodes[s["id"]]
try:
 end=time.monotonic()+30
 while time.monotonic()<end:
  if e.status=="failed":raise RuntimeError(e.events[-1])
  if e.snapshot and e.observe()["system"]["world"]["facts"]["geometry:block_a"]["status"]=="verified":break
  time.sleep(.2)
 e.submit_plan("plan",e.revision,{"nodes":[{"id":"pick","skill":{"name":"acquire_object","arguments":{"object":"block_a"}}},{"id":"place","skill":{"name":"place_object","arguments":{"object":"block_a","support":"platform"}},"depends_on":["pick"]}]})
 for ident in ["pick","place"]:
  end=time.monotonic()+6
  while True:
   sys=e.observe()["system"];node=next(n for n in sys["task"]["nodes"] if n["plan_node"]==ident)
   if node["status"]=="ready" or time.monotonic()>end:break
   time.sleep(.2)
  print("READY",json.dumps(node),flush=True)
  sid=e.execute_skill(ident,e.revision,sys["state_id"],node["id"],{"name":node["skills"][0],"arguments":node["arguments"]})
  deadline=time.monotonic()+120
  while e.active and time.monotonic()<deadline:time.sleep(.2)
  if e.active:e.cancel()
  seg=e.segments[sid];print("STAGE",json.dumps(dict(id=ident,execution=seg["execution"],error=seg.get("error"),effect=seg.get("node_verification"),calls=seg['api_attempts'])),flush=True)
  if seg['execution']!='completed':break
 time.sleep(1.);print("PUBLIC",json.dumps(e.finish(e.revision)),flush=True)
finally:e.close()
print("PHYSICAL",json.dumps(score_closed_batch(root)),flush=True)
