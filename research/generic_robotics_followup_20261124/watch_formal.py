import json,time,sys,os
from pathlib import Path
r=Path(__file__).parent;d=r/'formal-v1';last=int(sys.argv[1]);end=time.monotonic()+40;stamp=None;rows=[]
while True:
 p=d/'execution.json'
 try:
  m=p.stat().st_mtime_ns
  if m!=stamp:rows=json.loads(p.read_text());stamp=m
 except (FileNotFoundError,json.JSONDecodeError):pass
 if len(rows)!=last or time.monotonic()>=end or (d/'summary.json').exists():break
 time.sleep(1)
folders=sorted(p for p in d.iterdir() if p.is_dir() and p.name[:2].isdigit())
value=dict(completed=len(rows),total=54,recent=[dict(task=x['task'],seed=x['seed'],condition=x['condition'],status=x['result']['status']) for x in rows[-2:]])
if folders:
 f=folders[-1];value['active']=f.name
 try:
  s=json.loads((f/'workbench/current.json').read_text())['state'];value.update(status=s['status'],used=(s.get('episode') or {}).get('used'))
  value['subgoal']=s.get('subgoal','')[:160]
 except (FileNotFoundError,json.JSONDecodeError):pass
job=json.loads((r/'formal-job.json').read_text())
try:value['running']=Path('/proc')/str(job['pid']) in list(Path('/proc').iterdir()) and (Path('/proc')/str(job['pid'])/'stat').read_text().split()[2]!='Z'
except (FileNotFoundError,ProcessLookupError):value['running']=False
if (d/'summary.json').exists():value['summary']=json.loads((d/'summary.json').read_text())
if not value['running']:value['driver_tail']=(r/'formal-driver.log').read_text()[-1000:]
print(json.dumps(value,ensure_ascii=False))
