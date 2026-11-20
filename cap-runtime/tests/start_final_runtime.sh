#!/bin/bash
# Engineering harness inside the already allocated test job.
set -euo pipefail
project=/mnt/slurmfs-4090node1/homes/zchen897/projects/grad_robot_repair
cd "$project/agentic-mvp/cap-runtime"
export PYTHONPATH="$project/agentic-mvp/cap-runtime:$project/third_party/cap-x"
export ROBOT_DESCRIPTIONS_CACHE="$project/cache/robot_descriptions"
export CAP_RUNTIME_TOKEN="$(cat "$project/agentic-mvp/runtime-token.txt")"
exec "$project/.venv-capx/bin/python" -u -m agentic_cap.server --host 0.0.0.0 --port 18122 --upstream "$project/third_party/cap-x" --records "$project/agentic-mvp/final-records" --evaluation
