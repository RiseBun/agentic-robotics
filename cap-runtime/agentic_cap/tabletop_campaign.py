"""Frozen experiment design; never uses final seeds for candidate development."""
import copy
from .tabletop_domain import digest, LIMITS

DEV_SEEDS=(201,202)
VALIDATION_SEEDS=(301,302)
TEST_SEEDS=(701,702,703)
CONDITIONS=('direct_cap','system_no_memory','system_with_memory')
TASKS=[
 dict(id='hold',scene='tabletop_v1',text='Pick up the blue block and keep holding it.',
      goal={'all':[dict(predicate='holding',object='block_a')]}),
 dict(id='support',scene='tabletop_v1',text='Put the blue block on the grey platform and leave the gripper empty.',
      goal={'all':[dict(predicate='on',object='block_a',support='platform'),dict(predicate='empty_gripper')]}),
 dict(id='region',scene='tabletop_v1',text='Move the purple cylinder into the left marked region and release it. Leave the gripper empty.',
      goal={'all':[dict(predicate='in_region',object='cylinder_a',region='left'),dict(predicate='empty_gripper')]}),
 dict(id='unstack',scene='tabletop_stacked_v1',text='Remove the blue block from the yellow block and put it in the right marked region. Leave the gripper empty.',
      goal={'all':[dict(predicate='in_region',object='block_a',region='right'),dict(predicate='empty_gripper')]}),
 dict(id='reverse',scene='tabletop_stacked_v1',text='Reverse the stack: place the yellow block on the blue block and release. Leave the gripper empty.',
      goal={'all':[dict(predicate='on',object='block_b',support='block_a'),dict(predicate='empty_gripper')]}),
 dict(id='multiple',scene='tabletop_v1',text='Put the blue block in the left marked region and the purple cylinder in the right marked region. Leave the gripper empty.',
      goal={'all':[dict(predicate='in_region',object='block_a',region='left'),dict(predicate='in_region',object='cylinder_a',region='right'),dict(predicate='empty_gripper')]}),
]


def campaign_manifest(model_fingerprint,runtime_fingerprint,library_fingerprint):
    runs=[]
    for index,(task,seed) in enumerate((t,s) for t in TASKS for s in TEST_SEEDS):
        # Rotate condition order to avoid giving one condition every warm start.
        order=list(CONDITIONS[index%3:]+CONDITIONS[:index%3])
        for c in order:runs.append(dict(task=task['id'],scene=task['scene'],text=task['text'],seed=seed,condition=c))
    body=dict(schema=1,model_fingerprint=model_fingerprint,runtime_fingerprint=runtime_fingerprint,
        library_fingerprint=library_fingerprint,limits=copy.deepcopy(LIMITS),tasks=copy.deepcopy(TASKS),runs=runs,
        scoring='all episodes closed before independent evaluation; no truth exposed online')
    return dict(body,manifest_id=digest(body))


def admit_campaign(gates,manifest,current_library_fingerprint):
    for name in ('A','B','C','D_validation'):
        if gates.get(name,{}).get('status')!='passed':raise ValueError('Campaign blocked by gate '+name)
    if manifest.get('manifest_id')!=digest({k:v for k,v in manifest.items() if k!='manifest_id'}):raise ValueError('Frozen manifest was altered')
    if manifest.get('tasks')!=TASKS:raise ValueError('Frozen task descriptions or expected goals changed')
    expected=campaign_manifest(manifest['model_fingerprint'],manifest['runtime_fingerprint'],manifest['library_fingerprint'])
    if manifest!=expected:raise ValueError('Frozen comparison conditions changed')
    if current_library_fingerprint!=manifest['library_fingerprint']:raise ValueError('Frozen skill library changed')
    if len(manifest['runs'])!=54 or any(r['seed'] not in TEST_SEEDS for r in manifest['runs']):raise ValueError('Invalid frozen test design')
    return True
