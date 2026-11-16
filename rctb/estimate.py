"""Cost estimate for the model stages, computed from the folders already processed by `pages`.

Nothing is sent anywhere. For each processed page the requests that `transcribe`, `segment` and
`enrich` would make are sized (image tokens by the documented patch formula, text tokens by a
character heuristic), averaged per page, and scaled to the collection's page count.
Token counts are approximations (roughly +/-25 %); output length is the main unknown.
"""
from __future__ import annotations

import json
import math
import re

from PIL import Image

from .llm import load_prompt
from .models import Enrichment, Segmentation, Transcription, json_schema
from .s02_llm_stages import final_labels, labels_to_docs, load_pages
from .util import Config, load_files, read_jsonl

CJK = re.compile(r"[぀-ヿ㐀-鿿]")


def text_tokens(s: str) -> int:
    """~3.5 characters per token for alphabetic text, ~1 token per CJK character."""
    cjk = len(CJK.findall(s))
    return math.ceil(cjk + (len(s) - cjk) / 3.5)


def image_tokens(w: int, h: int, max_px: int) -> int:
    """ceil(w/28) x ceil(h/28) after fitting the long edge to max_px (Claude vision documentation)."""
    scale = min(1.0, max_px / max(w, h))
    return math.ceil(w * scale / 28) * math.ceil(h * scale / 28)


def run(cfg: Config) -> dict:
    pr, sc, ec = cfg["pricing"], cfg["segment"], cfg["enrich"]
    over = pr["overhead_tokens_per_call"]
    sys_tok = {n: text_tokens(load_prompt(cfg, n)) for n in ("transcribe", "segment", "enrich")}
    schema_tok = {"transcribe": text_tokens(json.dumps(json_schema(Transcription))),
                  "segment": text_tokens(json.dumps(json_schema(Segmentation))),
                  "enrich": text_tokens(json.dumps(json_schema(Enrichment, list(cfg["genres"]))))}
    tot = {s: {"in": 0, "out": 0, "calls": 0} for s in ("transcribe", "segment", "enrich")}
    n_pages = n_docs = 0
    all_files = load_files(cfg, None, need_pdf=False)
    for f in all_files:
        fn = f["file_number"]
        if not (cfg.work / "pages" / f"{fn}.jsonl").exists():
            continue
        pages = load_pages(cfg, fn)
        sizes = {r["page"]: Image.open(r["image_path"]).size for r in pages}
        n_pages += len(pages)
        for r in pages:  # one transcription call per page; output about as long as the page text
            draft = (r.get("text_tesseract") or r.get("text_embedded") or "")[:6000]
            tot["transcribe"]["in"] += (image_tokens(*sizes[r["page"]], cfg["llm"]["image_max_px"]) + text_tokens(draft)
                                        + sys_tok["transcribe"] + schema_tok["transcribe"] + over)
            tot["transcribe"]["out"] += int(text_tokens(r["text"]) * 1.15) + 60
            tot["transcribe"]["calls"] += 1
        step = sc["window"] - sc["overlap"]
        for start in range(0, len(pages), step):  # overlapping windows
            win = pages[start:start + sc["window"]]
            if start > 0 and len(win) <= sc["overlap"]:
                break
            tot["segment"]["in"] += (sum(image_tokens(*sizes[r["page"]], sc["image_max_px"]) + text_tokens(r["text"][:900]) for r in win)
                                     + sys_tok["segment"] + schema_tok["segment"] + over)
            tot["segment"]["out"] += 30 * len(win)
            tot["segment"]["calls"] += 1
        labels, _ = final_labels(cfg, fn)
        docs = labels_to_docs(labels)[0] if labels else [[r["page"]] for r in pages[::2]]  # no segmentation yet: assume 2 pages/doc
        known = {r["doc_id"]: r for r in read_jsonl(cfg.work / "enriched" / f"{fn}.jsonl")}
        by_page = {r["page"]: r for r in pages}
        for d in docs:
            n_docs += 1
            text = "\n\n".join(by_page[p]["text"] for p in d)[:ec["max_chars"]]
            tot["enrich"]["in"] += (sum(image_tokens(*sizes[p], cfg["llm"]["image_max_px"]) for p in d[:ec["max_images"]])
                                    + text_tokens(text) + sys_tok["enrich"] + schema_tok["enrich"] + over)
            e = known.get(f"{fn}-p{d[0]:03d}")
            tot["enrich"]["out"] += text_tokens(json.dumps(e["enrichment"], ensure_ascii=False)) if e else 900
            tot["enrich"]["calls"] += 1
    if not n_pages:
        raise SystemExit("Run `pages` on at least one folder first.")

    # ---- collection size: PDF page counts where present, index counts otherwise, mean for the rest
    counts = [f["n_pages"] or (int(f["index_pages"]) if f["index_pages"] else None) for f in all_files]
    known_counts = [c for c in counts if c]
    mean = sum(known_counts) / len(known_counts)
    total_pages = round(sum(c or mean for c in counts))
    print(f"Sample: {n_pages} pages, {n_docs} documents. Collection: {len(all_files)} folders, page count known for "
          f"{len(known_counts)} ({sum(known_counts)} pages, mean {mean:.0f}) -> about {total_pages} pages assumed.\n")
    print(f"{'stage':<11}{'calls/page':>11}{'input tok/page':>16}{'output tok/page':>17}")
    per_page = {}
    for s, t in tot.items():
        per_page[s] = (t["in"] / n_pages, t["out"] / n_pages)
        print(f"{s:<11}{t['calls'] / n_pages:>11.2f}{per_page[s][0]:>16.0f}{per_page[s][1]:>17.0f}")
    tin, tout = sum(v[0] for v in per_page.values()), sum(v[1] for v in per_page.values())
    print(f"{'all three':<11}{'':>11}{tin:>16.0f}{tout:>17.0f}\n")
    print(f"Whole collection: about {tin * total_pages / 1e6:.0f} M input and {tout * total_pages / 1e6:.0f} M output tokens.\n")
    print(f"{'model':<20}{'transcribe':>11}{'segment':>9}{'enrich':>8}{'total':>8}{'with batch':>12}   (USD)")
    result = {"total_pages": total_pages, "per_page": per_page, "models": {}}
    for model, (pin, pout) in pr["models"].items():
        cost = {s: (v[0] * pin + v[1] * pout) * total_pages / 1e6 for s, v in per_page.items()}
        total = sum(cost.values())
        result["models"][model] = {**cost, "total": total, "batch": total * pr["batch_discount"]}
        print(f"{model:<20}{cost['transcribe']:>11.0f}{cost['segment']:>9.0f}{cost['enrich']:>8.0f}{total:>8.0f}{total * pr['batch_discount']:>12.0f}")
    return result
