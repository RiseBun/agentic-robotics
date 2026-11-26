"""Synthetic authenticated HTTP fault probe; never counts as physical success."""
import json,secrets,threading,time,tempfile,urllib.request,urllib.error
from pathlib import Path
from agentic_cap.core import Manager
from agentic_cap.server import serve
from agentic_cap.tabletop_fixture import TabletopFixtureBackend

out=Path(__file__).parent
with tempfile.TemporaryDirectory() as directory:
 manager=Manager(directory,TabletopFixtureBackend);token=secrets.token_hex(24)
 server=serve(manager,'127.0.0.1',0,token);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
 base='http://127.0.0.1:'+str(server.server_address[1]);serial=0
 def request(route,body=None):
  req=urllib.request.Request(base+route,data=json.dumps(body).encode() if body is not None else None,headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
  try:
   with urllib.request.urlopen(req,timeout=10) as response:return response.status,json.load(response)
  except urllib.error.HTTPError as error:return error.code,json.load(error)
 def until(fn):
  end=time.monotonic()+5
  while time.monotonic()<end:
   if fn():return
   time.sleep(.01)
  raise RuntimeError('timeout')
 episode=None
 try:
  _,created=request('/episodes',dict(request_id='http-fault',scene='tabletop_v1',seed=201,goal={'all':[dict(predicate='on',object='block_a',support='platform'),dict(predicate='empty_gripper')]}))
  ident=created['id'];episode=manager.episodes[ident];prefix='/episodes/'+ident
  until(lambda:episode.status=='ready' and episode.snapshot)
  def planned(skill):
   global serial
   serial+=1
   status,result=request(prefix+'/plan',dict(request_id='plan'+str(serial),revision=episode.revision,plan={'nodes':[dict(id='new_name_'+str(serial),skill=skill)]}))
   assert status==200,result
   _,observation=request(prefix+'/observe');system=observation['system'];node=system['task']['nodes'][0]
   return dict(request_id='skill'+str(serial),revision=episode.revision,state_id=system['state_id'],task_node=node['id'],skill=skill),node
  def execute(skill):
   payload,node=planned(skill);status,result=request(prefix+'/skills',payload);assert status==202,result
   until(lambda:episode.state()['active_segment'] is None)
   return payload,result
  execute(dict(name='acquire_object',arguments={'object':'block_a'}))
  place=dict(name='place_object',arguments=dict(object='block_a',support='platform'))
  for i in range(2):
   episode.backend.refuse_release=True;last_payload,last_result=execute(place)
   if i==0:execute(dict(name='restore_grip_pose',arguments={'object':'block_a'}))
  payload,node=planned(place)
  before={k:episode.used[k] for k in ('calls','motion_steps')};count=len(episode.segments)
  blocked_status,blocked=request(prefix+'/skills',payload)
  code_status,code=request(prefix+'/segments',dict(request_id='different-code',revision=episode.revision,subgoal='renamed',code='p=plan_place("block_a",support="platform")',task_ref=dict(state_id=payload['state_id'],task_node=payload['task_node'])))
  replay_status,replay=request(prefix+'/skills',last_payload)
  after={k:episode.used[k] for k in before}
  cancel_status,cancel=request(prefix+'/cancel',{})
  _,state=request(prefix)
  results=dict(fixture_only=True,failed_placement_attempts=2,blocked_status=blocked_status,blocked_code=blocked.get('code'),bound_code_status=code_status,bound_code_error=code.get('code'),
    unchanged_calls_and_motion=before==after,unchanged_segment_count=count==len(episode.segments),idempotent_replay=replay_status==202 and replay==last_result,
    stop_confirmed=cancel_status==200 and cancel.get('stopped') is True,one_environment=episode.backend.reset_count==1,
    public_retry_constraint=node.get('retry_constraint'),scope='synthetic protocol fault, not a physical robot or model recovery score')
  assert blocked_status==409 and blocked.get('code')=='RECOVERY_BLOCKED',results
  assert code_status==409 and code.get('code')=='RECOVERY_BLOCKED',results
  assert all(results[k] for k in ['unchanged_calls_and_motion','unchanged_segment_count','idempotent_replay','stop_confirmed','one_environment']),results
  (out/'http-fault-result.json').write_text(json.dumps(results,indent=2));print(json.dumps(results))
 finally:
  if episode:episode.close()
  server.shutdown();server.server_close();thread.join(timeout=5)
