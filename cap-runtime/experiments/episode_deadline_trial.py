"""Real-CaP timeout convergence with no model and no privileged scoring.

Deadlines are shortened after initialization (and after motion starts for the
motion case). Costs retain initialization; no reset or budget reset is used.
"""
import argparse
import copy
import json
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from agentic_cap.cap_backend import CaPBackend
from agentic_cap.core import Manager
from agentic_cap.server import runtime_provenance
from experiments.active_view_trial import wait_for
from experiments.vertical_retreat_trial import source_hashes


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--upstream',required=True)
    p.add_argument('--records',type=Path,required=True)
    args=p.parse_args()
    root=args.records.resolve()
    root.mkdir(parents=True,exist_ok=False)
    cases=['no_client','hanging_http_api','expire_during_motion']
    (root/'protocol.json').write_text(json.dumps(dict(cases=cases,source_sha256=source_hashes(),
        runtime=runtime_provenance('cap',args.upstream),
        scope='real physics engineering timeout diagnosis, shortened deadline after ready; no LLM/benchmark/private scoring'),indent=2))
    rows=[]
    for index,mode in enumerate(cases):
        release=threading.Event()
        entered=threading.Event()
        class HangingHandler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length','1')
                self.end_headers()
                self.wfile.flush()
                entered.set()
                release.wait(15)
                try:self.wfile.write(b'x')
                except (BrokenPipeError,ConnectionResetError):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),HangingHandler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        class Backend(CaPBackend):
            def tools(self,segment):
                tools=super().tools(segment)
                def stalled_sensor():
                    with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}',timeout=15) as response:
                        response.read()
                    return {'status':'verified'}
                tools['diagnostic_sensor_request']=stalled_sensor
                return tools
        manager=Manager(root,lambda e:Backend(e,args.upstream))
        state=manager.create(f'case-{index}','cube_lifting',62,{'holding':'red cube'},limits={'wall_seconds':60})
        e=manager.episodes[state['id']]
        row=dict(mode=mode,episode_id=e.id,passed=False)
        try:
            wait_for(lambda:e.snapshot or e.status=='failed',55)
            if e.status=='failed':raise RuntimeError(str(e.events[-1]))
            if mode=='hanging_http_api':
                sid=e.execute('hang',e.revision,'hanging public sensor','diagnostic_sensor_request()\nclose_gripper()')
                if not entered.wait(5):raise RuntimeError('HTTP stall not reached')
            elif mode=='expire_during_motion':
                sid=e.execute('move',e.revision,'deadline in motion',
                    's=get_execution_state()\np=s["tcp_xyz"]\np[2]=p[2]+0.12\ngoto_pose(p,s["tcp_wxyz"])\nclose_gripper()')
                wait_for(lambda:e.motion or e.active is None,20)
                if e.active is None:raise RuntimeError('Motion interruption coverage missing')
            with e.lock:
                e.limits['wall_seconds']=time.monotonic()-e.started+.5
                e.event('diagnostic_deadline_shortened',limits=e.limits,mode=mode)
            wait_for(lambda:e.status=='closed',8)
            e.thread.join(3)
            if e.thread.is_alive():raise RuntimeError('Physics owner did not release')
            owner_exit_wall=time.monotonic()-e.started
            state=e.state()
            tick=e.backend.tick
            used=copy.deepcopy(state['used'])
            terminal=len(e.events)
            release.set()  # Let the uncooperative HTTP worker return late.
            time.sleep(.3)
            if e.backend.tick!=tick or e.state()['used']!=used:raise RuntimeError('Closed task continued stepping or accruing cost')
            if any(x['kind']=='api_started' for x in e.events[terminal:]):raise RuntimeError('Late code dispatched an API')
            if any(x['kind']=='api_started' and x.get('name')=='close_gripper' for x in e.events):
                raise RuntimeError('Post-deadline action dispatched')
            if e.active is not None or state['termination_reason']!='wall_budget_exhausted':raise RuntimeError('Deadline did not converge')
            if not next(x for x in e.events if x['kind']=='episode_closed').get('hold_confirmed'):
                raise RuntimeError('Close lacked owner hold confirmation')
            row.update(passed=True,overrun_seconds=used['wall_seconds']-state['limits']['wall_seconds'],
                       owner_exit_wall_seconds=owner_exit_wall,
                       cleanup_seconds=owner_exit_wall-used['wall_seconds'],
                       used=used,final_tick=tick,late_api_count=0)
        except Exception as exc:row['error']=str(exc)
        finally:
            release.set()
            e.close()
            server.shutdown();server.server_close()
            row['episode_state']=e.state()
            rows.append(row)
            (root/'results.json').write_text(json.dumps(rows,indent=2))
            print(json.dumps({k:row.get(k) for k in ['mode','episode_id','passed','overrun_seconds','error']}),flush=True)
    if not all(r['passed'] for r in rows):raise SystemExit(1)


if __name__=='__main__':main()
