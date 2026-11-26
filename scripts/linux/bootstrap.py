"""Install a user-owned ARM64 Node runtime and isolated test environment."""
import hashlib
import json
import os
import platform
import subprocess
import tarfile
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parents[2]
assert platform.system() == 'Linux'
arch = {'aarch64': 'arm64', 'x86_64': 'x64'}[platform.machine()]
index = json.load(urllib.request.urlopen('https://nodejs.org/dist/index.json', timeout=45))
version = next(row['version'] for row in index if row['version'].startswith('v24.'))
name = f'node-{version}-linux-{arch}'
tools = Path.home()/'.local/share/agentic-robotics/tools'
tools.mkdir(parents=True, exist_ok=True)
target = tools/name
if not target.exists():
    url = f'https://nodejs.org/dist/{version}/'
    archive = tools/(name+'.tar.xz')
    with urllib.request.urlopen(url+archive.name, timeout=90) as response, archive.open('wb') as output:
        while data := response.read(1024*1024):
            output.write(data)
    sums = urllib.request.urlopen(url+'SHASUMS256.txt', timeout=45).read().decode()
    expected = next(line.split()[0] for line in sums.splitlines() if line.split()[-1] == archive.name)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
        raise RuntimeError('Node archive digest mismatch')
    with tarfile.open(archive) as bundle:
        bundle.extractall(tools, filter='data')
link = tools/'node'
if not link.exists():
    link.symlink_to(target, target_is_directory=True)
env = {**os.environ, 'PATH': str(link/'bin')+os.pathsep+os.environ['PATH']}
subprocess.run([str(link/'bin/node'), '--version'], check=True)
venv = root/'.venv'
if not venv.exists():
    subprocess.run(['python3','-m','venv','--system-site-packages', str(venv)],check=True)
subprocess.run([str(venv/'bin/python'),'-m','pip','install','-r',str(root/'cap-runtime/requirements-test.txt')],check=True)
subprocess.run([str(link/'bin/npm'),'ci'],cwd=root/'frontend',env=env,check=True)
subprocess.run([str(link/'bin/npm'),'run','build'],cwd=root/'frontend',env=env,check=True)
print(json.dumps(dict(root=str(root), node=str(link/'bin/node'), python=str(venv/'bin/python'))))
