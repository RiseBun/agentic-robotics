"""Install the current CaP execution stack without CaP-X's x86 training lock.

Requires the pinned CaP-X checkout/submodules, migrated PyRoKi/jaxls sources,
checkpoints and robot descriptions. Never installs foreign-architecture wheels.
"""
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[2]
python = str(root/'.venv/bin/python')
capx = root/'third_party/cap-x'
packages = [root/'third_party/jaxls', root/'third_party/pyroki',
    capx/'capx/third_party/sam3', capx/'capx/third_party/contact_graspnet_pytorch',
    capx/'capx/third_party/robosuite', capx]
for package in packages:
    if not (package/'pyproject.toml').exists() and not (package/'setup.py').exists():
        raise RuntimeError(f'Missing migrated source: {package}')
subprocess.run([python, str(root/'scripts/linux/optional-sam3-video.py')], check=True)
subprocess.run([python, '-m', 'pip', 'install', '-r',
    str(root/'scripts/linux/requirements-simulation.txt')], check=True)
subprocess.run([python, '-m', 'pip', 'install', '--no-deps', 'pyrender==0.1.45'], check=True)
for package in packages:
    subprocess.run([python, '-m', 'pip', 'install', '--no-deps', '-e', str(package)], check=True)
