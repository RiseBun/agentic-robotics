#!/bin/bash
#SBATCH --job-name=cartesian-ik-diagnosis
#SBATCH --partition=GPU
#SBATCH --gres=gpu:4090:1
#SBATCH --nodelist=4090node1
#SBATCH --cpus-per-task=4
#SBATCH --mem=0
#SBATCH --time=00:08:00
#SBATCH --output=/mnt/slurmfs-4090node1/homes/zchen897/projects/grad_robot_repair/cartesian-ik-%j.log
set -euo pipefail
project=/mnt/slurmfs-4090node1/homes/zchen897/projects/grad_robot_repair
root="$project/agentic-mvp/cartesian-ik-diagnosis-v1"
export PYTHONPATH="$project/third_party/cap-x"
export ROBOT_DESCRIPTIONS_CACHE="$project/cache/robot_descriptions"
export JAX_PLATFORMS=cpu CUDA_VISIBLE_DEVICES='' OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
cd "$project"
"$project/.venv-capx/bin/python" -u "$root/replay.py" "$root"
