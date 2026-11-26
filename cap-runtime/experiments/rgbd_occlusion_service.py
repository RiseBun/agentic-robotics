"""Development-only image-plane sensor dropout over actual MuJoCo RGB-D.
No object poses or private evaluation data are read by the fault adapter.
"""
import argparse,json,os
import numpy as np
from agentic_cap.tabletop_backend import TabletopBackend
from agentic_cap.core import Manager
from agentic_cap.server import serve,runtime_provenance

# Fixed image rectangles, calibrated only from public development RGB-D.
RECTANGLES={'overview':(175,150,275,238),'wrist':(105,95,210,200)}

def apply_dropout(packet,mode):
    for name,camera in packet['cameras'].items():
        height,width=camera['depth'].shape
        x0,y0,x1,y1=(0,0,width,height) if mode=='persistent' and name=='wrist' else RECTANGLES[name]
        camera['rgb'][y0:y1,x0:x1]=0
        camera['depth'][y0:y1,x0:x1]=np.nan
    return packet

class OccludedBackend(TabletopBackend):
    def __init__(self,episode,evaluation=True):
        super().__init__(episode,evaluation=evaluation);self.dropout=False
        self.mode=os.environ.get('RGBD_OCCLUSION_MODE','partial')
    def transform_camera_packet(self,packet):
        if self.dropout:apply_dropout(packet,self.mode)
        return packet
    def tools(self,segment):
        tools=super().tools(segment);original=tools['execute_stage']
        def stage(ident,name):
            plan=self.plans.get(ident,{})
            if name=='place' and plan.get('kind')=='place' and not self.dropout:
                self.episode.checkpoint(segment);self.dropout=True
                self.episode.event('rgbd_dropout_started',mode=self.mode,rectangles=RECTANGLES,
                    scope='image-plane sensor dropout; not a physical occluding object')
            return original(ident,name)
        tools['execute_stage']=stage;return tools

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--records',required=True);parser.add_argument('--port',type=int,default=8121)
    parser.add_argument('--mode',choices=['partial','persistent'],default='partial');args=parser.parse_args()
    os.environ['RGBD_OCCLUSION_MODE']=args.mode
    backend=OccludedBackend
    if os.environ.get('RGBD_RESTORE_PLAN_PROBE','0')=='1':
        from .recorded_restore_probe import probed_backend
        backend=probed_backend(backend)
    manager=Manager(args.records,backend)
    server=serve(manager,'127.0.0.1',args.port,os.environ['CAP_RUNTIME_TOKEN'],runtime_provenance('cap'))
    print(json.dumps({'port':server.server_address[1],'experiment':'actual_rgbd_sensor_dropout'}),flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        for episode in manager.episodes.values():episode.close()
        server.server_close()
if __name__=='__main__':main()
