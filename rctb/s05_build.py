"""Stage 5 - build: assemble, check and publish the database.

For each folder: page texts + segmentation + enrichment (+ human corrections) -> Document records.
`full_text` is the concatenation of the pages' reference texts - written by code, never by a model.

Outputs in data/out/:
  json/<file_number>.json   one JSON per PDF (list of documents)
  rctb_documents.jsonl/.json  all documents merged
  rctb.sqlite               relational tables + full-text index (R: RSQLite, Python: sqlite3/pandas)
  tables/*.parquet          the same tables for arrow/pandas/polars
  qa_report.md              automatic checks, per folder
"""
from __future__ import annotations

import json
import re
import sqlite3

import pandas as pd
import yaml

from .models import Document
from .s02_llm_stages import final_labels, labels_to_docs, load_pages
from .util import Config, doc_id_for, load_files, log_run, norm_ws, read_jsonl, write_json, write_jsonl


def _grounded(name: str, text_norm: str) -> bool:
    """Does an extracted name literally occur in the document text (case/space-insensitive)?"""
    n = norm_ws(name).lower()
    return bool(n) and (n in text_norm or all(tok in text_norm for tok in re.findall(r"\w{3,}", n)))


def build_file(cfg: Config, f: dict, corrections: dict) -> tuple[list[dict], list[dict], list[str]]:
    fn = f["file_number"]
    pages = load_pages(cfg, fn)
    labels, seg_meta = final_labels(cfg, fn)
    issues: list[str] = []
    if not labels:
        for r in pages:
            r["doc_id"], r["is_blank"] = None, bool(r.get("blank_candidate"))
        return [], pages, [f"{fn}: no segmentation - pages only"]
    by_page = {r["page"]: r for r in pages}
    doc_lists, warn = labels_to_docs(labels)
    issues += [f"{fn}: {w}" for w in warn]
    enriched = {r["doc_id"]: r for r in read_jsonl(cfg.work / "enriched" / f"{fn}.jsonl")}
    blank = {l["page"] for l in labels if l["relation"] == "blank"}
    for p in sorted(blank):
        if len(by_page[p]["text"]) > 200:
            issues.append(f"{fn} p{p}: labelled blank but has {len(by_page[p]['text'])} characters of text")
    docs = []
    for doc_pages in doc_lists:
        doc_id = doc_id_for(fn, doc_pages[0])
        e = enriched.get(doc_id)
        if e is None or e["pages"] != doc_pages:
            issues.append(f"{doc_id}: no enrichment for pages {doc_pages} (run `enrich`)")
            continue
        full, offsets = "", {}
        for p in doc_pages:
            if full:
                full += "\n\n"
            offsets[str(p)] = len(full)
            full += by_page[p]["text"]
            by_page[p]["doc_id"] = doc_id
        fields = {**e["enrichment"], **corrections.get(doc_id, {})}
        doc = Document(doc_id=doc_id, file_number=fn, pages=doc_pages, start_page=doc_pages[0], end_page=doc_pages[-1],
                       full_text=full, page_offsets=offsets,
                       review_status="reviewed" if doc_id in corrections else e.get("review_status", "unreviewed"),
                       provenance={"pipeline_version": cfg["project"]["pipeline_version"], "config_sha": cfg.sha,
                                   "segmentation": seg_meta, "enrichment": e["provenance"],
                                   "text_layers": {str(p): by_page[p]["text_layer"] for p in doc_pages},
                                   "corrected_fields": sorted(corrections.get(doc_id, {}))},
                       **fields).model_dump()
        # ---- checks (flag, never silently fix)
        if doc["genre"] not in list(cfg["genres"]):
            issues.append(f"{doc_id}: genre {doc['genre']!r} not in the controlled vocabulary")
        lo = int((f.get("chartered") or "1900")[:4]) - 5
        hi = int((f.get("terminated") or "2000")[:4]) + 2
        for d in doc["dates"]:
            if d["value"] and not lo <= int(d["value"][:4]) <= hi:
                issues.append(f"{doc_id}: date {d['value']} outside the folder's expected range {lo}-{hi}")
        text_norm = norm_ws(full).lower()
        for etype, ents in doc["entities"].items():
            for ent in ents:
                ent["grounded"] = _grounded(ent["name"], text_norm)
        n_ent = sum(len(v) for v in doc["entities"].values())
        n_ok = sum(e_["grounded"] for v in doc["entities"].values() for e_ in v)
        if n_ent and n_ok / n_ent < 0.5:
            issues.append(f"{doc_id}: only {n_ok}/{n_ent} entity names found literally in the text (OCR noise or invented names?)")
        if not full.strip():
            issues.append(f"{doc_id}: empty full_text")
        docs.append(doc)
    kept_min, new_max = cfg["transcribe"].get("flag_kept_below", 0.85), cfg["transcribe"].get("flag_new_above", 0.25)
    for r in pages:
        ch = r.get("llm_changes") or {}
        if r["text_layer"] == "llm" and (ch.get("draft_words") or 0) >= 40:
            if ch["kept"] is not None and ch["kept"] < kept_min:
                issues.append(f"{fn} p{r['page']}: transcription keeps only {ch['kept']:.0%} of the OCR's words - check for dropped text")
            if ch["new"] is not None and ch["new"] > new_max:
                issues.append(f"{fn} p{r['page']}: {ch['new']:.0%} of the transcription's words are in no OCR reading - recovered text or invention?")
    for r in pages:
        r.setdefault("doc_id", None)
        r["is_blank"] = r["page"] in blank
        if r["doc_id"] is None and not r["is_blank"]:
            issues.append(f"{fn} p{r['page']}: page belongs to no document")
    return docs, pages, issues


DOC_COLS = ['doc_id', 'file_number', 'club', 'country', 'volume', 'start_page', 'end_page', 'n_pages', 'pages', 'genre', 'title', 'title_status', 'date_produced', 'date_received', 'date_modified', 'date_status', 'year', 'place_origin', 'place_origin_status', 'place_destination', 'place_destination_status', 'creators', 'recipients', 'languages', 'summary', 'has_illustration', 'has_table', 'n_items', 'notes', 'full_text', 'n_chars', 'review_status', 'provenance', 'page_offsets']


def _first_date(doc: dict, kind: str):
    return next((d["value"] for d in doc["dates"] if d["type"] == kind and d["value"]), None)


def run(cfg: Config, only=None) -> None:
    out = cfg.out
    out.mkdir(parents=True, exist_ok=True)
    cfile = cfg.p("corrections_dir") / "documents.yaml"
    corrections = (yaml.safe_load(cfile.read_text(encoding="utf-8")) or {}) if cfile.exists() else {}
    files = load_files(cfg, None, need_pdf=False)
    from .normalize import canonical_map
    canon = canonical_map(cfg)   # (type, name as written) -> canonical form, from corrections/entities.csv
    all_docs, all_pages, report = [], [], []
    for f in files:
        if not (cfg.work / "pages" / f"{f['file_number']}.jsonl").exists():
            continue
        docs, pages, issues = build_file(cfg, f, corrections)
        for d in docs:  # names stay as written; `canonical` groups their variants
            for etype, ents in d["entities"].items():
                for e in ents:
                    e["canonical"] = canon.get((etype, e["name"])) or e.get("normalized") or e["name"]
            for a in d["creators"] + d["recipients"]:
                t = "organizations" if a.get("kind") == "organization" else "persons"
                a["canonical"] = canon.get((t, a["name"])) or a["name"]
        write_json(out / "json" / f"{f['file_number']}.json", docs)
        all_docs += docs
        all_pages += pages
        layers = pd.Series([p["text_layer"] for p in pages]).value_counts().to_dict()
        report.append(f"## {f['file_number']} - {f['folder_title']}\n\n{len(pages)} pages, {len(docs)} documents, "
                      f"{sum(p['is_blank'] for p in pages)} blank; text layers used: {layers}\n\n"
                      + ("\n".join(f"- {i}" for i in issues) if issues else "- no issues flagged") + "\n")
    write_jsonl(out / "rctb_documents.jsonl", all_docs)
    write_json(out / "rctb_documents.json", all_docs)

    # ---------------- relational tables
    fmeta = {f["file_number"]: f for f in files}
    J = lambda x: json.dumps(x, ensure_ascii=False)
    t_docs, t_ent, t_agents, t_dates, t_ill, t_tab, t_items = [], [], [], [], [], [], []
    for d in all_docs:
        f = fmeta[d["file_number"]]
        produced = _first_date(d, "produced")
        any_date = produced or next((x["value"] for x in d["dates"] if x["value"]), None)
        t_docs.append({
            "doc_id": d["doc_id"], "file_number": d["file_number"], "club": f["club"], "country": f["country"],
            "volume": f["volume"], "start_page": d["start_page"], "end_page": d["end_page"], "n_pages": len(d["pages"]),
            "pages": J(d["pages"]), "genre": d["genre"], "title": d["title"]["value"], "title_status": d["title"]["status"],
            "date_produced": produced, "date_received": _first_date(d, "received"), "date_modified": _first_date(d, "modified"),
            "date_status": "unspecified" if not d["dates"] else
                           ("uncertain" if any(x["certainty"] == "uncertain" for x in d["dates"]) else "specified"),
            "year": int(any_date[:4]) if any_date else None,
            "place_origin": d["place_origin"]["value"], "place_origin_status": d["place_origin"]["status"],
            "place_destination": d["place_destination"]["value"], "place_destination_status": d["place_destination"]["status"],
            "creators": "; ".join(a["name"] + (f" ({a['role']})" if a["role"] else "") for a in d["creators"]),
            "recipients": "; ".join(a["name"] + (f" ({a['role']})" if a["role"] else "") for a in d["recipients"]),
            "languages": ",".join(d["languages"]), "summary": d["summary"], "has_illustration": d["has_illustration"],
            "has_table": d["has_table"], "n_items": len(d["items"]), "notes": d["notes"], "full_text": d["full_text"], "n_chars": len(d["full_text"]),
            "review_status": d["review_status"], "provenance": J(d["provenance"]), "page_offsets": J(d["page_offsets"])})
        for etype, ents in d["entities"].items():
            t_ent += [{"doc_id": d["doc_id"], "type": etype, **e} for e in ents]
        for rel in ("creators", "recipients"):
            t_agents += [{"doc_id": d["doc_id"], "relation": rel[:-1], **a} for a in d[rel]]
        t_dates += [{"doc_id": d["doc_id"], **x} for x in d["dates"]]
        t_ill += [{"doc_id": d["doc_id"], **x} for x in d["illustrations"]]
        t_tab += [{"doc_id": d["doc_id"], **x} for x in d["tables"]]
        t_items += [{"doc_id": d["doc_id"], "item_no": i, **x} for i, x in enumerate(d["items"], 1)]
    pf = {}
    for f in files:
        for r in read_jsonl(cfg.work / "pagefacts" / f"{f['file_number']}.jsonl"):
            pf[(f["file_number"], r["page"])] = r
    t_pages = [{"file_number": p["file_number"], "page": p["page"], "doc_id": p["doc_id"], "is_blank": p["is_blank"],
                "page_kind": pf.get((p["file_number"], p["page"]), {}).get("page_kind"),
                "heading": pf.get((p["file_number"], p["page"]), {}).get("heading"),
                "image": p["image"], "text": p["text"], "text_layer": p["text_layer"], "tess_conf": p.get("tess_conf"),
                "tess_langs": p.get("tess_langs"), "scripts": J(p.get("scripts", {})),
                "llm_draft": ",".join(p.get("llm_draft_layers") or []) or None,
                "llm_kept": (p.get("llm_changes") or {}).get("kept"), "llm_new": (p.get("llm_changes") or {}).get("new"),
                "text_llm": p["text"] if p["text_layer"] == "llm" else None,
                "text_embedded": p.get("text_embedded"), "text_tesseract": p.get("text_tesseract"),
                "text_provided": p.get("text_provided")} for p in all_pages]
    t_files = [{**{k: v for k, v in f.items() if k != "flags"}, "flags": "; ".join(f["flags"])} for f in files]
    cols = {
        "entities": ["doc_id", "type", "name", "canonical", "role", "normalized", "grounded"],
        "agents": ["doc_id", "relation", "name", "canonical", "role", "kind"],
        "dates": ["doc_id", "type", "value", "as_written", "certainty"],
        "items": ["doc_id", "item_no", "label", "source", "date", "date_as_written", "page", "first_words"],
        "illustrations": ["doc_id", "page", "alt_text"], "tables_md": ["doc_id", "page", "caption", "markdown"]}
    tables = {"files": pd.DataFrame(t_files), "documents": pd.DataFrame(t_docs, columns=DOC_COLS), "pages": pd.DataFrame(t_pages),
              "entities": pd.DataFrame(t_ent, columns=cols["entities"]), "agents": pd.DataFrame(t_agents, columns=cols["agents"]),
              "dates": pd.DataFrame(t_dates, columns=cols["dates"]),
              "illustrations": pd.DataFrame(t_ill, columns=cols["illustrations"]),
              "tables_md": pd.DataFrame(t_tab, columns=cols["tables_md"]),
              "items": pd.DataFrame(t_items, columns=cols["items"])}
    db = out / "rctb.sqlite"
    db.unlink(missing_ok=True)
    con = sqlite3.connect(db)
    (out / "tables").mkdir(exist_ok=True)
    for name, df in tables.items():
        df.to_sql(name, con, index=False)
        df.to_parquet(out / "tables" / f"{name}.parquet", index=False)
    if t_docs:
        con.execute("CREATE VIRTUAL TABLE documents_fts USING fts5(doc_id UNINDEXED, title, summary, full_text, "
                    "tokenize='unicode61 remove_diacritics 2')")
        con.execute("INSERT INTO documents_fts SELECT doc_id, title, summary, full_text FROM documents")
        con.execute("CREATE INDEX idx_entities_name ON entities(name)")
        con.execute("CREATE INDEX idx_pages_doc ON pages(doc_id)")
    con.commit()
    con.close()
    (out / "qa_report.md").write_text("# RCTB quality report\n\nAutomatic checks only; they flag, they do not fix.\n\n"
                                      + "\n".join(report), encoding="utf-8")
    if all_pages and not all_docs:
        print("build: no documents yet - the database holds the pages only (segment and enrich have not produced records)")
    log_run(cfg, "build", n_documents=len(all_docs), n_pages=len(all_pages))
    print(f"build: {len(all_docs)} documents, {len(all_pages)} pages -> {out}")
