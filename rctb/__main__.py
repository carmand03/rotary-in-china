"""Command line: python -m rctb <stage> [--files B37-F01 B10-F03] [--config config.yaml]"""
from __future__ import annotations

import argparse
import json

from .util import Config


def main() -> None:
    ap = argparse.ArgumentParser(prog="rctb", description=__doc__)
    ap.add_argument("stage", choices=["inventory", "pages", "transcribe", "segment", "enrich", "normalize", "build", "embed",
                                      "all", "status", "estimate", "load-gold", "evaluate", "search", "ask", "schema"])
    ap.add_argument("arg", nargs="?", help="gold file (load-gold, evaluate) or query (search, ask)")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--files", nargs="*", help="restrict to these file numbers")
    ap.add_argument("--pages", nargs="*", type=int, help="transcribe: only these page numbers (for trials)")
    ap.add_argument("--force", action="store_true", help="pages: redo even if up to date")
    ap.add_argument("-k", type=int, default=5)
    ap.add_argument("--language", default=None, help="ask: en or zh")
    ap.add_argument("--mode", default="hybrid", choices=["keyword", "semantic", "hybrid"])
    a = ap.parse_args()
    cfg = Config(a.config)

    from . import s00_inventory, s01_pages, s02_llm_stages as llm_stages, s05_build, search

    if a.stage in ("inventory", "all"):
        s00_inventory.run(cfg)
    if a.stage in ("pages", "all"):
        s01_pages.run(cfg, a.files, a.force)
    def has_model(stage):
        return ({**cfg["llm"], **(cfg["llm"].get("stages") or {}).get(stage, {})})["backend"] != "none"

    if a.stage == "transcribe" or (a.stage == "all" and has_model("transcribe")):
        llm_stages.transcribe(cfg, a.files, a.pages)
    if a.stage == "segment" or (a.stage == "all" and has_model("segment")):
        llm_stages.segment(cfg, a.files)
    if a.stage == "enrich" or (a.stage == "all" and has_model("enrich")):
        llm_stages.enrich(cfg, a.files)
    if a.stage in ("normalize", "all"):
        from . import normalize
        normalize.run(cfg)
    if a.stage in ("build", "all"):
        s05_build.run(cfg, a.files)
    if a.stage in ("embed", "all"):
        search.embed(cfg)
    if a.stage == "status":
        from . import status
        status.run(cfg, a.files)
    if a.stage == "estimate":
        from . import estimate
        estimate.run(cfg)
    if a.stage == "load-gold":
        llm_stages.load_gold(cfg, a.arg)
    if a.stage == "evaluate":
        from . import evaluate
        evaluate.run(cfg, a.arg)
    if a.stage == "schema":
        from .models import Document, json_schema
        (cfg.base / "schema" / "document.schema.json").write_text(json.dumps(json_schema(Document, list(cfg["genres"])), indent=2))
        print("schema/document.schema.json written")
    if a.stage == "search":
        tb = search.TextBase(cfg.out, a.config)
        df = getattr(tb, a.mode)(a.arg, k=a.k)
        print(df[[c for c in ("doc_id", "club", "genre", "date_produced", "title", "pages", "snippet", "text") if c in df]]
              .to_string(max_colwidth=90))
    if a.stage == "ask":
        tb = search.TextBase(cfg.out, a.config)
        r = tb.ask(a.arg, k=a.k, language=a.language or cfg["rag"]["answer_language"])
        print(r["answer"], "\n")
        for c in r["citations"]:
            print(f"  [{c['source']}] {c['doc_id']} pp.{c['pages']} {'✓' if c['verified'] else '✗ NOT FOUND VERBATIM'}  “{c['quote']}”")


if __name__ == "__main__":
    main()
