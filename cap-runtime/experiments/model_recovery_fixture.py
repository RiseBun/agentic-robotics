"""Synthetic fault service for real-model recovery probes. NOT physical evaluation."""
import argparse,json,os
from agentic_cap.core import Manager
from agentic_cap.server import serve
from agentic_cap.tabletop_fixture import TabletopFixtureBackend

class RecoveryFixture(TabletopFixtureBackend):
    def __init__(self,episode):
        super().__init__(episode);self.fault_active=False;self.fault_triggered=False;self.fault_views=0
        self.mode=os.environ['MODEL_RECOVERY_FAULT']
    def verify(self,goal,snapshot):
        if self.fault_active and any(c.get('object')=='platform' and c['predicate'] in {'visible','geometry','support_geometry'} for c in goal['all']):
            return dict(status='unknown',reason='support_evidence_unavailable_after_approach',fixture_only=True)
        return super().verify(goal,snapshot)
    def tools(self,segment):
        tools=super().tools(segment);original_gripper=tools['set_gripper'];original_inspect=tools['inspect_object']
        def gripper(command):
            self.episode.checkpoint(segment)
            plan=self.pending
            if command=='open' and plan and plan.get('kind')=='place' and self.held is not None:
                if not self.fault_triggered:
                    self.fault_active=self.fault_triggered=True
                    self.episode.event('fixture_fault_injected',fault='support_evidence_loss',fixture_only=True)
                if self.fault_active:return dict(status='unknown',reason='support_evidence_missing_at_release; obtain current support evidence before release',fixture_only=True)
            return original_gripper(command)
        def inspect(obj,evidence='geometry'):
            if obj!='platform' or not self.fault_active:return original_inspect(obj,evidence)
            if self.fault_views>=2:return dict(status='unknown',views_used=0,reason='distinct_fixture_views_exhausted',fixture_only=True)
            self.occluded=True;self.restore_after_view=self.mode=='recoverable'
            try:
                result=original_inspect(obj,evidence)
                self.fault_views+=result.get('views_used',0)
                if result['status']=='available':
                    self.fault_active=False
                    self.episode.event('fixture_fault_resolved',reason='actual_inspection_view',fixture_only=True)
                return result
            finally:self.occluded=False
        tools.update(set_gripper=gripper,inspect_object=inspect)
        return tools

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--records',required=True);parser.add_argument('--port',type=int,default=0);parser.add_argument('--mode',choices=['recoverable','persistent'],required=True);args=parser.parse_args()
    os.environ['MODEL_RECOVERY_FAULT']=args.mode
    manager=Manager(args.records,RecoveryFixture)
    server=serve(manager,'127.0.0.1',args.port,os.environ['CAP_RUNTIME_TOKEN'])
    print(json.dumps({'port':server.server_address[1],'fixture_only':True}),flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        for episode in manager.episodes.values():episode.close()
        server.server_close()
if __name__=='__main__':main()
