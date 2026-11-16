"""Progress report: what is done, what is left, how fast it is going. Reads files only; safe at any time."""
from __future__ import annotations

import datetime as dt
import json

from .s02_llm_stages import final_labels, labels_to_docs
from .util import Config, load_files, read_json, read_jsonl


def run(cfg: Config, only=None) -> dict:
    files = load_files(cfg, only, need_pdf=False)
    tot = {"folders": 0, "pages": 0, "transcribed": 0, "segmented": 0, "docs": 0, "enriched": 0}
    print(f"{'folder':<10}{'pages':>7}{'transcribed':>13}{'segmented':>11}{'documents':>11}{'enriched':>10}")
    for f in files:
        fn = f["file_number"]
        pages = read_jsonl(cfg.work / "pages" / f"{fn}.jsonl")
        if not pages:
            continue
        tdir = cfg.work / "transcripts" / fn
        n_tr = len(list(tdir.glob("p*.json"))) if tdir.exists() else 0
        labels, _ = final_labels(cfg, fn)
        docs = labels_to_docs(labels)[0] if labels else []
        n_en = len(read_jsonl(cfg.work / "enriched" / f"{fn}.jsonl"))
        tot["folders"] += 1; tot["pages"] += len(pages); tot["transcribed"] += n_tr
        tot["segmented"] += bool(labels); tot["docs"] += len(docs); tot["enriched"] += n_en
        if not (n_tr == len(pages) and labels and n_en == len(docs)) or only:
            print(f"{fn:<10}{len(pages):>7}{n_tr:>13}{'yes' if labels else '-':>11}{len(docs) or '-':>11}{n_en or '-':>10}")
    print(f"{'TOTAL':<10}{tot['pages']:>7}{tot['transcribed']:>13}{str(tot['segmented']) + '/' + str(tot['folders']):>11}"
          f"{tot['docs']:>11}{tot['enriched']:>10}   (folders that are complete are not listed)\n")

    # speed: model calls finished in the last 30 minutes, from the cache
    now, recent, secs = dt.datetime.now(dt.timezone.utc), {}, {}
    for path in (cfg.work / "llm_cache").glob("*/*.json"):
        if now.timestamp() - path.stat().st_mtime > 1800:
            continue
        meta = (read_json(path) or {}).get("meta", {})
        stage = path.parent.name
        recent[stage] = recent.get(stage, 0) + 1
        if (meta.get("usage") or {}).get("seconds"):
            secs.setdefault(stage, []).append(meta["usage"]["seconds"])
    if recent:
        print("Last 30 minutes: " + ", ".join(
            f"{s} {n} calls" + (f" (~{sum(secs[s]) / len(secs[s]):.0f} s each)" if s in secs else "") for s, n in recent.items()))
        left = tot["pages"] - tot["transcribed"]
        if recent.get("transcribe") and left:
            print(f"Transcription: {left} pages left, about {left / (recent['transcribe'] * 2):.1f} h at the current rate.")
    else:
        print("No model calls in the last 30 minutes.")
    fails = [r for r in read_jsonl(cfg.work / "runs.jsonl") if r["stage"].endswith("-failures")][-3:]
    for r in fails:
        print(f"{r['time']} {r['stage']}: {r['n']} failed call(s), e.g. {r['examples'][0][:150]}")
    warn = 0
    for path in (cfg.work / "llm_cache").glob("*/*.json"):
        if '"warning"' in path.read_text(encoding="utf-8")[:1500]:
            warn += 1
    if warn:
        print(f"{warn} cached call(s) carry a warning (context window full or reply cut off): raise llm.num_ctx / max_tokens.")
    return tot
