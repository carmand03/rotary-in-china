#!/bin/bash
# Submit from the project folder:  sbatch hpc/job_h100.slurm            (all folders)
#                                  sbatch hpc/job_h100.slurm B37-F01    (one folder, for the pilot)
# Partition names are from the CC-IN2P3 Slurm documentation (Nov 2026). The --gres line is the usual
# Slurm form but its exact spelling at CC-IN2P3 is NOT verified: check with user support or `sinfo -o "%P %G"`.
#SBATCH --job-name=rctb
#SBATCH --partition=gpu_h100
#SBATCH --gres=gpu:h100:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=1-00:00:00
#SBATCH --output=logs/rctb-%j.log

# ADAPT: activate the environment created by hpc/setup.sh
source "$HOME/rctb-env/bin/activate"
export HF_HOME=${HF_HOME:-$HOME/hf-cache}     # ADAPT: a storage area with room for ~70 GB of model weights

cd "$SLURM_SUBMIT_DIR"
bash hpc/run_pipeline.sh "$@"
