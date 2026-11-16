#!/bin/bash
# Start a local model server on this machine's GPU, run the model stages against it, stop the server.
# Works inside a Slurm job (hpc/job_h100.slurm) and in a terminal of a GPU Jupyter session.
#   bash hpc/run_pipeline.sh                 # all folders
#   bash hpc/run_pipeline.sh B37-F01 B10-F03 # some folders
# Everything is resumable: finished calls are in data/work/llm_cache and are never repeated.
set -euo pipefail
cd "$(dirname "$0")/.."
CONFIG=${CONFIG:-config.vllm.yaml}
STAGES=${STAGES:-"transcribe segment enrich"}
FILES_ARG=""; [ "$#" -gt 0 ] && FILES_ARG="--files $*"

MODEL=$(python3 -c "import yaml,sys; print(yaml.safe_load(open('$CONFIG'))['llm']['model'])")
PORT=${PORT:-$(( 20000 + (${SLURM_JOB_ID:-$$} % 20000) ))}
export RCTB_BASE_URL="http://127.0.0.1:${PORT}/v1"
export RCTB_LOCAL_KEY=${RCTB_LOCAL_KEY:-$(python3 -c "import secrets; print(secrets.token_hex(16))")}  # compute nodes are shared
export HF_HUB_OFFLINE=${HF_HUB_OFFLINE:-1}     # weights must already be in $HF_HOME (see hpc/README.md)
mkdir -p logs

# ADAPT if needed: check the flags with `vllm serve --help` for the installed version.
SERVER_CMD=${SERVER_CMD:-"vllm serve $MODEL --host 127.0.0.1 --port $PORT --api-key $RCTB_LOCAL_KEY --seed 0 --max-model-len 32768 --limit-mm-per-prompt {\"image\":10}"}
echo "[rctb] $(date -Is) host=$(hostname) model=$MODEL port=$PORT config=$CONFIG"
command -v nvidia-smi >/dev/null && nvidia-smi --query-gpu=name,memory.total --format=csv,noheader || true
command -v vllm >/dev/null && echo "[rctb] vllm $(vllm --version 2>/dev/null || true)"

$SERVER_CMD > "logs/server-${SLURM_JOB_ID:-$$}.log" 2>&1 &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null || true' EXIT

echo "[rctb] waiting for the model server (loading weights can take several minutes)"
for i in $(seq 1 ${WAIT_STEPS:-180}); do
  if curl -sf --noproxy '*' -H "Authorization: Bearer $RCTB_LOCAL_KEY" "$RCTB_BASE_URL/models" >/dev/null 2>&1; then READY=1; break; fi
  kill -0 $SERVER_PID 2>/dev/null || { echo "[rctb] server stopped; see logs/server-*.log"; exit 1; }
  sleep ${WAIT_SLEEP:-10}
done
[ "${READY:-0}" = 1 ] || { echo "[rctb] server not ready in time"; exit 1; }

for stage in $STAGES; do
  echo "[rctb] $(date -Is) stage $stage"
  python3 -m rctb "$stage" --config "$CONFIG" $FILES_ARG
done
python3 -m rctb build --config "$CONFIG"
python3 -m rctb embed --config "$CONFIG"
echo "[rctb] $(date -Is) done"
