"""Register localhost-only Linux services and a native desktop launcher."""
import json
import os
import secrets
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[2]
home = Path.home()
data = home/'.local/share/agentic-robotics/data'
data.mkdir(parents=True, exist_ok=True, mode=0o700)
logs = data.parent/'logs'
logs.mkdir(parents=True, exist_ok=True, mode=0o700)
token = data/'runtime-token.txt'
if not token.exists():
    token.write_text(secrets.token_urlsafe(32))
    token.chmod(0o600)
settings = data/'robot-settings.json'
if not settings.exists():
    settings.write_text(json.dumps(dict(runtimeURL='http://127.0.0.1:8120')))
node = home/'.local/share/agentic-robotics/tools/node/bin/node'
unitroot = home/'.config/systemd/user'
unitroot.mkdir(parents=True, exist_ok=True)
web = f'''[Unit]
Description=Agentic Robotics local browser workbench
After=network-online.target

[Service]
Type=simple
WorkingDirectory={root}
Environment=EMBUDDIES_DATA_DIR={data}
Environment=ROBOT_WEB_PORT=8787
ExecStart={node} {root}/runtime/robot/web-host.mjs
StandardOutput=append:{logs}/web.log
StandardError=inherit
Restart=on-failure
RestartSec=3
TimeoutStopSec=15

[Install]
WantedBy=default.target
'''
(unitroot/'agentic-robotics-web.service').write_text(web)
dependencies = []
for service in ['sam3', 'grasp', 'pyroki']:
    name = f'agentic-{service}.service'
    dependencies.append(name)
    (unitroot/name).write_text(f'''[Unit]
Description=Agentic Robotics local {service} inference
After=network-online.target

[Service]
Type=simple
WorkingDirectory={root}
Environment=CAP_PROJECT_ROOT={root}
Environment=ROBOT_DESCRIPTIONS_CACHE={root}/cache/robot_descriptions
Environment=JAX_PLATFORMS=cpu
Environment=XLA_PYTHON_CLIENT_PREALLOCATE=false
Environment=MUJOCO_GL=egl
Environment=PYOPENGL_PLATFORM=egl
ExecStart={root}/.venv/bin/python -u {root}/cap-runtime/scripts/capx_services.py {service}
StandardOutput=append:{logs}/{service}.log
StandardError=inherit
Restart=on-failure
RestartSec=5
TimeoutStopSec=15
''')
runtime = f'''[Unit]
Description=Agentic Robotics CaP Runtime (local)
Requires={' '.join(dependencies)}
After=network-online.target {' '.join(dependencies)}

[Service]
Type=simple
WorkingDirectory={root}/cap-runtime
Environment=CAP_PROJECT_ROOT={root}
Environment=MUJOCO_GL=egl
Environment=PYOPENGL_PLATFORM=egl
Environment=JAX_PLATFORMS=cpu
Environment=ROBOT_DESCRIPTIONS_CACHE={root}/cache/robot_descriptions
Environment=CAP_ACTIVE_EVIDENCE=1
Environment=CAP_PLACEMENT_EVIDENCE=1
Environment=CAP_HOLDING_WATCHDOG=1
EnvironmentFile={data}/runtime.env
ExecStartPre={root}/.venv/bin/python {root}/scripts/linux/wait-services.py
ExecStart={root}/.venv/bin/python -u -m agentic_cap.server --backend cap --port 8120 --upstream {root}/third_party/cap-x --records {data.parent}/robot-records
StandardOutput=append:{logs}/runtime.log
StandardError=inherit
KillSignal=SIGINT
Restart=on-failure
RestartSec=3
TimeoutStopSec=15
TimeoutStartSec=210

[Install]
WantedBy=default.target
'''
(unitroot/'agentic-cap-runtime.service').write_text(runtime)
envfile = data/'runtime.env'
envfile.write_text('CAP_RUNTIME_TOKEN='+token.read_text().strip()+'\n')
envfile.chmod(0o600)
launcher = root/'scripts/linux/open-workbench.sh'
launcher.write_text('#!/bin/sh\nset -eu\nsystemctl --user start agentic-robotics-web.service\n'
    'if command -v firefox >/dev/null 2>&1; then\n'
    '  exec firefox --new-tab http://127.0.0.1:8787\n'
    'fi\nexec xdg-open http://127.0.0.1:8787\n')
launcher.chmod(0o755)
applications = home/'.local/share/applications'
applications.mkdir(parents=True, exist_ok=True)
desktop = applications/'agentic-robotics.desktop'
desktop.write_text(f'''[Desktop Entry]
Type=Application
Name=Agentic Robotics
Comment=机器人任务工作台
Exec={launcher}
Terminal=false
Categories=Development;Science;
''')
subprocess.run(['systemctl','--user','daemon-reload'],check=True)
# Configure only the Runtime credential through the existing encrypted store.
script = root/'scripts/linux/configure-runtime.mjs'
subprocess.run([str(node), str(script), str(data)],check=True,env={**os.environ,'EMBUDDIES_DATA_DIR':str(data)})
subprocess.run(['systemctl','--user','enable','--now','agentic-robotics-web.service'],check=True)
print(json.dumps(dict(url='http://127.0.0.1:8787', launcher=str(desktop),
    cap_service='registered; start only after simulation dependencies and model services are ready')))
