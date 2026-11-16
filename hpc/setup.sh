#!/bin/bash
# One-time setup, on a machine of the computing centre that has internet access (login/interactive node).
# NOT tested on CC-IN2P3: paths and module names will need adapting; read before running.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv "$HOME/rctb-env"
source "$HOME/rctb-env/bin/activate"
pip install --upgrade pip
pip install -r requirements.txt sentence-transformers
pip install vllm                                   # model server (large install; needs a recent CUDA)

export HF_HOME=${HF_HOME:-$HOME/hf-cache}
MODEL=$(python3 -c "import yaml; print(yaml.safe_load(open('config.vllm.yaml'))['llm']['model'])")
huggingface-cli download "$MODEL"                  # vision-language model for the three model stages
huggingface-cli download BAAI/bge-m3               # multilingual embeddings for search

# The `pages` stage needs poppler (pdftoppm, pdftotext) and tesseract with eng, jpn, chi_tra, rus.
# If they are not installed on the cluster, the simplest route is to run `python -m rctb inventory`
# and `python -m rctb pages` on your own computer and copy data/work and data/out/images to the cluster:
# the GPU stages need neither tool.
command -v tesseract >/dev/null && tesseract --list-langs || echo "tesseract not found: run the pages stage elsewhere (see above)"
command -v pdftoppm  >/dev/null || echo "poppler not found: run the pages stage elsewhere (see above)"
