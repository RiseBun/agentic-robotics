"""Robosuite tabletop scene. Truth is used only for scene construction/private scoring."""
import numpy as np
import xml.etree.ElementTree as ET
from robosuite.environments.manipulation.lift import Lift
from robosuite.environments.manipulation.manipulation_env import ManipulationEnv
from robosuite.models.arenas import TableArena
from robosuite.models.objects import BoxObject, CylinderObject
from robosuite.models.tasks import ManipulationTask
from .tabletop_scene import OBJECTS,REGIONS


class GenericTabletop(Lift):
    def __init__(self,*args,scene='tabletop_v1',**kwargs):
        self.scene_name=scene;self.public_objects={};self.dimensions={}
        super().__init__(*args,**kwargs)

    def _load_model(self):
        ManipulationEnv._load_model(self)
        self.robots[0].robot_model.set_base_xpos(self.robots[0].robot_model.base_xpos_offset['table'](self.table_full_size[0]))
        arena=TableArena(table_full_size=self.table_full_size,table_friction=self.table_friction,table_offset=self.table_offset)
        arena.set_origin([0,0,0]);objs=[]
        for name,region in REGIONS.items():
            lo,hi=np.array(region['bounds_xy']);center=(lo+hi)/2;half=(hi-lo)/2
            arena.worldbody.append(ET.Element('geom',name='region_'+name,type='box',
                pos=' '.join(map(str,[*center,.8005])),size=' '.join(map(str,[*half,.0005])),
                rgba='0.15 0.55 0.35 0.25',contype='0',conaffinity='0',group='1'))
        sizes={'block_a':[.022,.022,.022],'block_b':[.035,.025,.025],
               'cylinder_a':[.023,.03],'platform':[.065,.065,.015]}
        scale=float(self.rng.uniform(.95,1.05))
        for ident,d in OBJECTS.items():
            if ident=='table':continue
            size=np.array(sizes[ident])*scale
            cls=CylinderObject if d['shape']=='cylinder' else BoxObject
            obj=cls(name=ident,size=size,rgba=d['rgba'],density=500,rng=self.rng)
            self.public_objects[ident]=obj;self.dimensions[ident]=size;objs.append(obj)
        self.cube=self.public_objects['block_a']
        self.model=ManipulationTask(mujoco_arena=arena,mujoco_robots=[r.robot_model for r in self.robots],mujoco_objects=objs)

    def _setup_references(self):
        ManipulationEnv._setup_references(self)
        self.object_body_ids={k:self.sim.model.body_name2id(v.root_body) for k,v in self.public_objects.items()}

    def _setup_observables(self):
        return ManipulationEnv._setup_observables(self)

    def _reset_internal(self):
        ManipulationEnv._reset_internal(self)
        positions={'block_a':[-.06,-.16], 'block_b':[.04,-.03], 'cylinder_a':[-.05,.17], 'platform':[.17,.12]}
        for ident,obj in self.public_objects.items():
            xy=np.array(positions[ident])+self.rng.uniform(-.012,.012,2)
            halfheight=float(self.dimensions[ident][-1]);z=float(self.table_offset[2])+halfheight+.002
            if self.scene_name=='tabletop_stacked_v1' and ident=='block_a':
                xy=np.array(positions['block_b']);z=float(self.table_offset[2])+2*self.dimensions['block_b'][-1]+halfheight+.003
            if self.scene_name=='tabletop_stacked_v1' and ident=='block_b':xy=np.array(positions[ident])
            self.sim.data.set_joint_qpos(obj.joints[0],np.r_[xy,z,1.,0.,0.,0.])

    def reward(self,action=None):return 0.
    def _check_success(self):return False
