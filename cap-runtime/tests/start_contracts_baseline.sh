#!/bin/bash
set -euo pipefail
project=/mnt/slurmfs-4090node1/homes/zchen897/projects/grad_robot_repair
export PYTHONPATH="$project/agentic-mvp/contracts-baseline/cap-runtime:$project/third_party/cap-x"
export ROBOT_DESCRIPTIONS_CACHE="$project/cache/robot_descriptions"
export CAP_RUNTIME_TOKEN="$(cat "$project/agentic-mvp/runtime-token.txt")"
export MUJOCO_GL=egl PYOPENGL_PLATFORM=egl JAX_PLATFORMS=cpu XLA_PYTHON_CLIENT_PREALLOCATE=false
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 CUBLAS_WORKSPACE_CONFIG=:4096:8
exec "$project/.venv-capx/bin/python" -u -m agentic_cap.server --host 0.0.0.0 --port 18122 --upstream "$project/third_party/cap-x" --records "$project/agentic-mvp/contracts-baseline-records" --evaluation
