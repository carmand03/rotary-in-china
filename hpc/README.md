# Running RCTB on the Huma-Num / CC-IN2P3 GPU platform

Route in use: a Jupyter session on a GPU node, Ollama loaded with `module load ollama`, and the
notebook `notebooks/rctb_huma_num.ipynb` driving the pipeline as a background process.

Status: every cell of the notebook was run on a development machine against a stand-in Ollama
server, including stopping a run midway and restarting it. Nothing has been run at CC-IN2P3 or
with a real model yet.

## One-time preparation

1. Upload `rctb_humanum.zip` to your space (e.g. `/sps/humanum/home/<login>/`) and unzip it there. It contains the
   code, prompts, notebook, configuration and the index, and no data. Then add your page folders:
   - `rctb/data/raw/pages/AC0033-B37-F01_folder/image_0001.png`, `image_0001.txt` ... (one folder per PDF;
     if they are already on the cluster, a symbolic link is enough: `ln -s /path/to/your/folders rctb/data/raw/pages`)
   - The PDFs are **not** needed: the page count, images and starting text all come from these folders.
     (If you do add PDFs under `data/raw/pdf/`, their built-in text layer is kept as one more layer.)
2. In a terminal of a GPU session: `module load ollama`, then `ollama list`.
   `qwen3.5:35b`, which you already have, accepts images and is the configured model.
   Optional second candidate: `ollama pull qwen3-vl:32b` (21 GB).
3. If cell 2 reports missing Python packages, run cell 2b once: it installs them into `.pydeps/` inside the project.

Poppler and Tesseract are not required on the cluster: the page images are made from your PNGs and
the starting text is your Google Vision files, which the vision model then corrects against the image. If Tesseract is available it adds a second
OCR layer.

## Running

Open `notebooks/rctb_huma_num.ipynb` and run cells 1 to 3.

- **Pilot first:** `FILES = ["B37-F01"]` (the default). Then cell 7 scores the segmentation against
  `gold/B37-F01.yaml` and shows the records; cell 8 compares models on that folder.
- **Whole collection:** `FILES = None`, run cells 1 to 3.
- **Session limit (23 h):** when the session ends, the run is killed with it. Start a new
  session and run cells 1 to 3 again. The run continues where it stopped:
  each transcribed page and each model reply is saved the moment it arrives, and saved work is
  never recomputed. At most the few requests in flight at the moment of the stop are redone.
- **Batches:** none to manage by hand. Folders are processed in index order. If you prefer to
  finish some clubs first, list them in `FILES`.
- **Two sessions at once** (to go faster) are safe if they work on different folders
  (`FILES = [...]` in each, same project folder). Do not run `build`/`embed` in both at the
  same time: let one finish, or run cell 3 once more at the end with `FILES = None`.
- Cell 5 shows progress, speed, failed calls and context-window warnings at any time.

## Settings (`config.hpc.yaml`)

| Setting | Meaning |
|---|---|
| `llm.model` | Ollama model for all model stages; recorded in every record's provenance |
| `llm.stages` | per-stage model, e.g. `enrich: {model: "llama3.3:70b", images: false}` for a text-only model |
| `llm.num_ctx` | context window (32768). Cell 5 warns if prompts fill it |
| `llm.think` | `false` for qwen3.x (direct answers); `null` for model families without a thinking mode |
| `llm.workers` | parallel requests (2). More only helps if the Ollama server allows it (`OLLAMA_NUM_PARALLEL`) |
| `segment.method` | `features` (recommended for local models), `pairwise` or `window` |
| `llm.stages.entities` | a different model for entity extraction, e.g. `{model: "qwen3:30b", images: false}` (text only) |
| `enrich.entities_chunk_chars` | size of the text pieces read for entities (6000); smaller = more thorough, more calls |
| `transcribe.only_if` | `always`, or `low_quality` to transcribe only pages whose OCR looks poor |

## Alternative: Slurm batch job with vLLM

`hpc/job_h100.slurm`, `hpc/run_pipeline.sh` and `config.vllm.yaml` run the same stages as an
`sbatch` job with a vLLM server, which batches requests more efficiently than Ollama. They need
vLLM installed and are untested at CC-IN2P3; use them only if the notebook route is too slow.

## Checking the pilot and taking the results home

- `python -m rctb evaluate gold/B37-F01.yaml --config config.hpc.yaml` (notebook cell 7) writes
  `data/out/eval/B37-F01_segmentation.html`: every page as a thumbnail with the reference and predicted
  document boundaries side by side, disagreements highlighted with the model's stated reason. The CSV
  files beside it hold the same per page, and a field-by-field comparison of the metadata.
- Segmentation method: `segment.method: window` (several pages per call) or `pairwise` (one call per
  page, each page compared with the one before). After changing it, run the `segment` stage and
  `evaluate` again to compare.
- Semantic search: `ollama pull bge-m3`, then `embedding.backend: ollama` in `config.hpc.yaml`.
- To use the viewer on your own computer, copy `data/out/` (database, page images, search index,
  reports) and `corrections/entities.csv`:
  `tar czf rctb_results.tar.gz data/out corrections/entities.csv`, download it, and unpack it inside the
  project folder there.
