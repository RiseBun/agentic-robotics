#!/bin/bash
#SBATCH --job-name=runtime-deadline-diag
#SBATCH --partition=GPU
#SBATCH --gres=gpu:4090:2
#SBATCH --nodelist=4090node1
#SBATCH --cpus-per-task=8
#SBATCH --mem=0
#SBATCH --time=00:15:00
#SBATCH --output=/mnt/slurmfs-4090node1/homes/zchen897/projects/grad_robot_repair/runtime-deadline-%j.log
set -euo pipefail
project=/mnt/slurmfs-4090node1/homes/zchen897/projects/grad_robot_repair
root="$project/agentic-mvp/runtime-deadline-v1"
cd "$project"
export MUJOCO_GL=egl PYOPENGL_PLATFORM=egl JAX_PLATFORMS=cpu XLA_PYTHON_CLIENT_PREALLOCATE=false
export ROBOT_DESCRIPTIONS_CACHE="$project/cache/robot_descriptions"
export PYTHONPATH="$root/cap-runtime:$project/third_party/cap-x"
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 CUBLAS_WORKSPACE_CONFIG=:4096:8
python_bin="$project/.venv-capx/bin/python"
CUDA_VISIBLE_DEVICES=0 "$python_bin" -u capx_services.py sam3 > "$root/sam3-$SLURM_JOB_ID.log" 2>&1 & sam=$!
CUDA_VISIBLE_DEVICES=1 "$python_bin" -u capx_services.py grasp --deterministic-seed 0 > "$root/grasp-$SLURM_JOB_ID.log" 2>&1 & grasp=$!
CUDA_VISIBLE_DEVICES='' "$python_bin" -u capx_services.py pyroki > "$root/pyroki-$SLURM_JOB_ID.log" 2>&1 & ik=$!
trap 'kill "$sam" "$grasp" "$ik" 2>/dev/null || true' EXIT
"$python_bin" - <<'PY'
import socket,time
for port in [8114,8115,8116]:
    end=time.monotonic()+360
    while True:
        try:
            with socket.create_connection(('127.0.0.1',port),timeout=1): break
        except OSError:
            if time.monotonic()>end: raise TimeoutError(port)
            time.sleep(2)
PY
CUDA_VISIBLE_DEVICES=0 "$python_bin" -u -m experiments.episode_deadline_trial \
    --upstream "$project/third_party/cap-x" --records "$root/records" > "$root/trial-$SLURM_JOB_ID.log" 2>&1
