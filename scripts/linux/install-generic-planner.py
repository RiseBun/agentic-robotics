"""Install pinned typed clients and an isolated Thor cuRobo provider."""
import json
import subprocess
import sys
from pathlib import Path

root=Path(__file__).resolve().parents[2]
if not (root/'.venv/bin/python').exists():raise SystemExit('Install the baseline Thor simulation environment first')
manifest=json.loads((root/'docs/generic-robotics-dependencies.json').read_text())
for name,entry in manifest['dependencies'].items():
    directory=root/'third_party'/name
    fresh=not directory.exists()
    if fresh:subprocess.run(['git','clone','--no-checkout',entry['repository'],str(directory)],check=True)
    if not fresh and subprocess.check_output(['git','-C',str(directory),'status','--porcelain'],text=True):raise SystemExit('Modified dependency: '+name)
    subprocess.run(['git','-C',str(directory),'checkout','--detach',entry['commit']],check=True)
venv=root/'.venv-curobo'
if not venv.exists():subprocess.run([str(root/'.venv/bin/python'),'-m','venv','--system-site-packages',str(venv)],check=True)
version=f'{sys.version_info.major}.{sys.version_info.minor}'
site=venv/f'lib/python{version}/site-packages'
(site/'existing-thor-packages.pth').write_text(str(root/f'.venv/lib/python{version}/site-packages')+'\n')
python=venv/'bin/python'
for dependency in ['curobo','aspire']:
    subprocess.run([str(python),'-m','pip','install','--no-deps','-e',str(root/'third_party'/dependency)],check=True)
subprocess.run([str(python),'-m','pip','install','--no-deps','cuda-core==1.3.1','warp-lang==1.19.0',
    'numpy-quaternion==2024.0.13','setuptools_scm==10.3.4','vcs-versioning==2.6.0','importlib_resources==7.1.0','cuda-bindings==13.5.0',
    'nvidia-nvvm==13.2.78','nvidia-nvfatbin==13.2.78','nvidia-cudla==13.5.25'],check=True)
subprocess.run([str(root/'.venv/bin/python'),'-m','pip','install','--no-deps','-e',str(root/'third_party/aspire')],check=True)
headers=root/f'.venv/lib/python{version}/site-packages/nvidia/cu13'
if not (headers/'include/nvrtc.h').exists():raise SystemExit('Existing Thor NVRTC headers not found; system CUDA was not changed')
unitroot=Path.home()/'.config/systemd/user';unitroot.mkdir(parents=True,exist_ok=True)
logroot=Path.home()/'.local/share/agentic-robotics/logs';logroot.mkdir(parents=True,exist_ok=True)
lines=['[Unit]','Description=Agentic Robotics isolated cuRobo planning provider','After=network-online.target','',
    '[Service]','Type=simple',f'WorkingDirectory={root}','Environment=CUDA_VISIBLE_DEVICES=0',
    f'Environment=CUDA_PATH={headers}',f'Environment=CUDA_HOME={headers}',
    f'ExecStart={python} -u -m cap_harness.providers.curobo.service --host 127.0.0.1 --port 8118',
    f'StandardOutput=append:{logroot}/curobo.log','StandardError=inherit','Restart=on-failure','RestartSec=5',
    'TimeoutStopSec=15','','[Install]','WantedBy=default.target','']
(unitroot/'agentic-curobo.service').write_text('\n'.join(lines))
subprocess.run(['systemctl','--user','daemon-reload'],check=True)
print(json.dumps(dict(status='installed_not_started',service='agentic-curobo.service',
    next='Stop any manually launched provider before starting this service; repeat the planning compatibility probe.')))
