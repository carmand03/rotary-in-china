# Rotary in China Textual Database (RCTB)

A reproducible pipeline that turns the 63 scanned folders of Rotary club archives (China and
Manchuria, 1919-1952) into a document-level text database that can be queried from Python or R,
searched by keyword or meaning, questioned through a RAG system, and read image-and-text side by side.

## Status

See the table "State of the work" in the [main README](../README.md).

## Design principles

1. **The page is the ground truth, the document is the unit of analysis.** Every page keeps its image
   and all its text layers. Documents are lists of pages (not necessarily consecutive) laid over them.
2. **`full_text` is assembled by code, never written by a model.** It is the concatenation of the
   reference texts of the document's pages. The model only decides boundaries and describes. This is
   what guarantees "verbatim, not regenerated".
3. **Every model call is cached and logged** (`data/work/llm_cache/`): prompt hash, model, token usage,
   date. Re-running costs nothing and gives identical output; changing a prompt re-runs only what
   depends on it. Prompts are plain Markdown files in `prompts/`.
4. **Uncertainty is data.** Titles and places carry a status (`specified`, `inferred`, `uncertain`,
   `not_specified`, `not_applicable`); each date keeps its role, the form in which it is written on
   the page, and a certainty flag.
5. **Human corrections live apart from machine output** (`corrections/`) and always win. The pipeline
   never overwrites them, so the whole database can be rebuilt at any time.
6. **Checks flag, they do not fix** (`data/out/qa_report.md`).

## Pipeline

```
rotary_index.xlsx + PDFs
   │  inventory    files.jsonl: club, volume, ISO dates, checksum, page count, index mismatches
   ▼
   │  pages        page image + text layers: embedded (in the PDF) · provided (your txt) · tesseract
   ▼
   │  transcribe   vision model reads each page image verbatim                    [model, optional]
   ▼
   │  segment      where each document starts. Three methods (config `segment.method`):
   │               features = the model describes each page, fixed rules place the boundaries;
   │               pairwise = each page compared with the one before; window = several pages per call  [model]
   ▼
   │  enrich       per document, two calls: (1) description from images + text: genre, title, dates,
   │               places, creators, recipients, languages, summary, tables, illustrations;
   │               (2) named entities from the text alone, read piece by piece                        [model]
   ▼
   │  normalize    name variants → canonical entities (rules; authority table you can edit)
   ▼
   │  build        + corrections → validation → JSON per PDF, merged JSONL, SQLite (+FTS), Parquet
   ▼
   │  embed        passages (with page numbers) → vector index
   ▼
 Python / R  ·  search (keyword, semantic, hybrid)  ·  RAG with verified quotes  ·  viewer
```

## Quick start

```bash
pip install -r requirements.txt          # plus poppler and tesseract, see requirements.txt
# put the index at data/raw/rotary_index.xlsx, PDFs in data/raw/pdf/ (AC0033-B37-F01.pdf ...),
# and your page folders in data/raw/pages/ (AC0033-B37-F01_folder/image_0001.png, image_0001.text ...)

make sample                              # reproduces the worked example, no model needed
python -m rctb search "exchange rate" --mode keyword
python examples/query_python.py
make app                                 # viewer at http://localhost:8501
```

Full run, once a model is configured in `config.yaml` (`llm.backend`, `llm.model`):

```bash
export ANTHROPIC_API_KEY=...
python -m rctb all --files B37-F01       # test on one folder first
python -m rctb evaluate gold/B37-F01.yaml   # segmentation vs. the reviewed reference
python -m rctb all                       # then everything
```

Every stage can be run alone (`python -m rctb pages|transcribe|segment|enrich|build|embed`) and
limited with `--files`. Each run appends a line to `data/work/runs.jsonl` (stage, time, config hash,
git commit, tool versions).

## What the sample showed about OCR

The text layer inside the sample PDF is weak: typewritten letters are read letter-spaced
("H a r b i n , J u l y"), Japanese is garbled, and 8 of 46 pages have no text at all. A fresh
Tesseract pass with the right languages reads the typescript and the Japanese roster far better, but
still fails on handwriting, stamps, engraved script and multi-column reading order. So:

- `pages` keeps every layer and records quality indicators per page;
- `text_layer_priority` in `config.yaml` decides which becomes the reference text
  (`corrected > llm > provided > tesseract > embedded`: your Google Vision files rank above Tesseract);
- the `transcribe` stage gives a vision model the page image together with the Google Vision
  reading (and Tesseract's as a second opinion) and asks it to correct that reading against the
  image: reading order, misplaced fragments, misread words, missed stamps and handwriting.
  It is a correction of the OCR, not a fresh transcription;
- each transcription records how far it moved from the OCR it started from: the share of the OCR's
  words it kept and the share of its own words found in no OCR reading. `build` flags pages
  outside the thresholds in `qa_report.md`, and the viewer shows the changes word by word.
  Trial on a few pages: `python -m rctb transcribe --files B37-F01 --pages 1 4 9`.

Known Tesseract normalisation: spaces it inserts between CJK characters are removed.

Your existing text files (`image_0001.text`) are read as the `provided` layer and can be added
at any time: `pages` re-reads them without repeating the OCR. Page images are re-rendered from the
PDFs as compact JPEGs unless `existing_pages.use_png` is set.

## Cost of the model stages

`python -m rctb estimate` sizes the requests the three model stages would make for the folders
already processed, and scales them to the collection (prices are set in `config.yaml`, nothing is
sent). From the sample folder, at about 12,000 pages for the collection: roughly 115 M input and
10 M output tokens. Treat the output side as a lower bound (the sample's reference records are
leaner than a model's would be) and re-run the estimate once all PDFs are inventoried.
The pipeline calls the standard API; the half-price Batch API is not implemented yet.

## Free local alternative

`config.local.yaml` runs every model stage on your own machine through a local server such as
[Ollama](https://ollama.com): nothing leaves the computer and nothing is billed.

```bash
ollama pull qwen3-vl:8b
OLLAMA_CONTEXT_LENGTH=16384 ollama serve          # the default context is too small for page images
pip install sentence-transformers                 # local multilingual embeddings (bge-m3)
python -m rctb all --config config.local.yaml --files B37-F01
python -m rctb evaluate gold/B37-F01.yaml --config config.local.yaml
RCTB_CONFIG=config.local.yaml streamlit run app/app.py
```

- `llm.stages` lets each stage use its own model, e.g. a small OCR-specialised model for
  `transcribe` (`plain_text: true`, prompt in `prompts/transcribe_plain.md`) and a larger general
  model for `enrich` and `rag`.
- Replies from local models are checked against the record structure and re-asked up to
  `retries` times; decoding uses temperature 0 and a fixed seed.
- Caching, logging, corrections and outputs are identical to the API route, and results from
  different models can be compared with `evaluate` on the same reference folder.

### On the Huma-Num / CC-IN2P3 GPUs

`config.hpc.yaml` and `notebooks/rctb_huma_num.ipynb` run the same stages in a GPU Jupyter session
at CC-IN2P3 with Ollama (`module load ollama`). The run is a background process that can be stopped
at any time, or be cut by the session limit, and continues where it stopped when started again.
`python -m rctb status` reports progress. See `hpc/README.md`.

The connection code was tested against a stand-in server that imitates a local model's replies;
no real local model has been run on these pages yet.

## Records (`data/out/`)

`json/<file_number>.json` (one per PDF), `rctb_documents.jsonl` / `.json` (merged), `rctb.sqlite`,
`tables/*.parquet`, `images/`, `index/`, `qa_report.md`. JSON Schema: `schema/document.schema.json`.

| Field | Content |
|---|---|
| `doc_id` | `B37-F01-p008`: file number + first page. Stable when other documents are re-segmented |
| `file_number`, `pages`, `start_page`, `end_page` | location in the PDF; `pages` may be non-consecutive |
| `genre` | controlled vocabulary in `config.yaml` (your seven + `periodical`, `certificate`, `administrative_record`) |
| `title` | `{value, status}`; `inferred` = descriptive title supplied for an untitled item |
| `dates` | list of `{type: produced/received/modified/other, value: YYYY[-MM[-DD]], as_written, certainty}` |
| `place_origin`, `place_destination` | `{value, status}` |
| `creators`, `recipients` | lists of `{name, role, kind}` |
| `languages` | ISO 639-1 codes |
| `full_text`, `page_offsets` | verbatim text; offset of each page inside it (for page-exact citation) |
| `summary` | English |
| `has_table`, `tables` | flag; Markdown transcription per table, kept beside (not instead of) the text |
| `has_illustration`, `illustrations` | flag; `{page, alt_text}` |
| `items` | only for sheets carrying several separate items (clippings, slips): `{label, source, date, date_as_written, page, first_words}` |
| `entities` | `persons`, `organizations`, `locations`, `events`, `works`: `{name, canonical, role, normalized, grounded}` |
| `notes`, `review_status`, `provenance` | reviewer notes; unreviewed/reviewed; who or what produced each part |

SQLite / Parquet tables: `files`, `documents`, `pages`, `entities`, `agents`, `dates`,
`illustrations`, `tables_md`, `items`, plus the full-text index `documents_fts`. In `documents`, the first
date of each type is flattened into `date_produced`, `date_received`, `date_modified`, and `year`.

`grounded` on an entity means its name occurs literally in the document text. Ungrounded names
come either from OCR noise in the text or from a model inventing or normalising a name; both
deserve a look, and the QA report lists documents where most entities are ungrounded.

## Entity normalisation

Names are stored exactly as written, so one person appears as "Y. Gundsi", "Yoshio Gundsi" and
"Gundsi, Y.". `python -m rctb normalize` (part of `all`, no model involved) groups such variants in
an authority table, `corrections/entities.csv`, and `build` adds a `canonical` value to every entity
and to creators and recipients. The viewer's entity filter and the network examples use it.

| status | meaning | applied |
|---|---|---|
| `auto` | strong rule: same family name with compatible initials or given names; same name ignoring case, punctuation and word order ("Harbin Rotary Club" = "ROTARY CLUB OF HARBIN") | yes |
| `suggested` | weaker evidence: family name alone, similar spelling (possible OCR variant), "Harbin, Manchuria" → "Harbin" | no, until you decide |
| `confirmed` | your decision; edit `canonical` freely (for example to keep "Hsinking" rather than "Changchun") | yes, and never changed by the pipeline |
| `rejected` | your decision: the variant stays on its own and is not proposed again | no |

Open the CSV in Excel or any editor, change `status` (and `canonical` if needed), save, and run
`python -m rctb normalize && python -m rctb build`. Each row shows the rule that produced it, the
number of documents and the clubs where the variant occurs, so that namesakes in different clubs
can be told apart before merging. Rerunning `normalize` after new folders are processed adds the
new variants and keeps your decisions.

## Correcting by hand

| To change | Edit | Then |
|---|---|---|
| the text of a page | `corrections/pages/B37-F01/p0027.txt` | `build`, `embed` |
| document boundaries | `corrections/segments/B37-F01.yaml` | `enrich` (new documents only), `build`, `embed` |
| any field of a document | `corrections/documents.yaml` | `build` |
| which name variants are the same entity | `corrections/entities.csv` | `normalize`, `build` |

Examples are in `corrections/README.md`. A document with corrected fields becomes `reviewed`.

## Design decisions to confirm

- **Several items on one sheet** (address slips pasted on a card, two or more clippings) are one
  document whose `items` list describes each item (headline, newspaper, date, first words), so
  individual clippings can be found and counted. Their text stays together in the sheet's `full_text`.
- **Cover letter and enclosed form are two documents**, linked for now only through `notes`.
- **Blank pages** stay in `pages` (`is_blank`) and belong to no document.
- **Tesseract Chinese model is `chi_tra`** (traditional), appropriate for the period.

## Layout

```
config.yaml  config.local.yaml  config.hpc.yaml  hpc/  notebooks/   prompts/   rctb/ (code)   app/ (viewer)   examples/   gold/   corrections/   tests/
data/raw/     inputs (not versioned)
data/work/    intermediate results, model cache, run log
data/out/     the database
```
