"""Stages 2-4 - the three steps that use a language model.

  transcribe : page image -> verbatim text (one call per page)           [optional]
  segment    : sequence of pages -> where each document starts and ends
  enrich     : one document -> genre, title, dates, people, summary, entities...

Each stage writes plain JSON under data/work/ and can be re-run alone. Prompts live in prompts/
as editable Markdown; their hash is stored with every result.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .llm import LLM, load_prompt
from .models import (EnrichCore, Enrichment, Entities, PageFacts, PairLabel, Segmentation, Transcription,
                     json_schema as _json_schema, require_all)


def json_schema(model, genres=None):
    """Schema as sent to the model: every field required (see models.require_all)."""
    return require_all(_json_schema(model, genres))
from .util import Config, doc_id_for, load_files, log_run, read_json, read_jsonl, sha256_text, write_json, write_jsonl


def pmap(fn, items, workers: int) -> list:
    """Run fn over items, `llm.workers` requests at a time, keeping order. Servers that batch
    requests (vLLM, the Claude API) process several pages at once; results are identical."""
    def safe(x):
        try:
            return fn(x)
        except Exception as e:  # one bad page must not stop a run of thousands; it is retried on the next run
            try:
                where = FAIL_LABEL[0](x)
            except Exception:
                where = "?"
            FAILURES.append(f"[{where}] {type(e).__name__}: {str(e)[:200]}")
            return None

    if workers <= 1 or len(items) <= 1:
        return [safe(x) for x in items]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(safe, items))


FAILURES: list[str] = []
FAIL_LABEL = [lambda x: "?"]   # set by each stage: how to name the page or document a failed call was about


def report_failures(cfg: Config, stage: str) -> None:
    if FAILURES:
        print(f"{stage}: {len(FAILURES)} call(s) failed and were skipped; run the stage again to retry them.")
        for line in FAILURES[:20]:
            print("   " + line)
        log_run(cfg, f"{stage}-failures", n=len(FAILURES), examples=FAILURES[:20])
        FAILURES.clear()


# ------------------------------------------------------------------ page text
def page_text(cfg: Config, rec: dict) -> tuple[str, str]:
    """Reference text of a page and the layer it came from, following text_layer_priority."""
    fn, p = rec["file_number"], rec["page"]
    for layer in cfg["text_layer_priority"]:
        if layer == "corrected":
            f = cfg.p("corrections_dir") / "pages" / fn / f"p{p:04d}.txt"
            if f.exists():
                return f.read_text(encoding="utf-8").strip(), "corrected"
        elif layer == "llm":
            t = read_json(cfg.work / "transcripts" / fn / f"p{p:04d}.json")
            if t:
                rec["llm_changes"], rec["llm_draft_layers"] = t.get("changes"), t.get("draft_layers")
                return t["text"].strip(), "llm"
        elif rec.get(f"text_{layer}"):
            return rec[f"text_{layer}"].strip(), layer
    return "", "none"


def load_pages(cfg: Config, fn: str) -> list[dict]:
    pages = read_jsonl(cfg.work / "pages" / f"{fn}.jsonl")
    for r in pages:
        r["text"], r["text_layer"] = page_text(cfg, r)
        r["image_path"] = cfg.out / r["image"]
    return pages


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[^\W_]+", text.lower()) if len(w) > 1 or not w.isascii()]


def compare_texts(all_drafts: str, output: str, first_draft: str) -> dict:
    """Word-level comparison of a transcription with the OCR it started from (order is ignored,
    so re-ordering a page costs nothing). kept = share of the first draft's words still present;
    new = share of the transcription's words found in no draft (recovered text, or invention)."""
    from collections import Counter

    d1, out, known = Counter(_words(first_draft)), Counter(_words(output)), Counter(_words(all_drafts))
    kept = sum((d1 & out).values()) / sum(d1.values()) if d1 else None
    new = sum((out - known).values()) / sum(out.values()) if out else None
    return {"draft_words": sum(d1.values()), "output_words": sum(out.values()),
            "kept": None if kept is None else round(kept, 3), "new": None if new is None else round(new, 3)}


# ------------------------------------------------------------------ 2. transcribe
def transcribe(cfg: Config, only=None, only_pages=None) -> None:
    llm, tc = LLM(cfg, "transcribe"), cfg["transcribe"]
    plain = bool(llm.c.get("plain_text"))  # OCR-specialised models: their own short prompt, plain text back
    system = "" if plain else load_prompt(cfg, "transcribe")
    schema = None if plain else json_schema(Transcription)
    for f in load_files(cfg, only):
        fn, todo = f["file_number"], []
        for r in read_jsonl(cfg.work / "pages" / f"{fn}.jsonl"):
            out = cfg.work / "transcripts" / fn / f"p{r['page']:04d}.json"
            if out.exists() or (only_pages and r["page"] not in only_pages):
                continue
            if tc["only_if"] == "low_quality" and (r.get("tess_conf") or 0) >= tc["min_tess_conf"]:
                continue
            todo.append((r, out))

        def work(job, f=f):
            r, out = job
            if plain:
                user = load_prompt(cfg, llm.c.get("prompt_file", "transcribe_plain")).strip()
            else:
                order = [l for l in tc.get("draft_priority", ["provided", "tesseract", "embedded"]) if (r.get(f"text_{l}") or "").strip()]
                drafts = order[:2] if tc.get("second_draft", True) else order[:1]
                user = f"Folder: Rotary Club of {f['club']} ({f['country']}), page {r['page']} of {f['n_pages']}.\n"
                if not drafts:
                    user += "No OCR reading is available for this page: transcribe it from the image."
                for i, layer in enumerate(drafts):
                    user += (f"\n{'OCR reading to start from' if i == 0 else 'Second OCR reading, from another engine, for comparison'}:\n"
                             f"<ocr_{i + 1}>\n{r[f'text_{layer}'][:tc.get('draft_max_chars', 12000)]}\n</ocr_{i + 1}>\n")
            res, meta = llm.call("transcribe", system, user, [cfg.out / r["image"]], schema,
                                 validate=None if plain else Transcription)
            rec = Transcription(**res).model_dump()
            if not plain:  # how far the model moved away from the OCR it started from (checked in `build`)
                rec["draft_layers"] = drafts
                rec["changes"] = compare_texts(" ".join(r[f"text_{l}"] for l in drafts), rec["text"],
                                               r[f"text_{drafts[0]}"] if drafts else "")
            write_json(out, {**rec, "provenance": meta})

        FAIL_LABEL[0] = lambda job, fn=fn: f"{fn} p{job[0]['page']}"
        pmap(work, todo, int(llm.c.get("workers", 1)))
        print(f"transcribe: {fn} - {len(todo)} new pages")
    report_failures(cfg, "transcribe")
    log_run(cfg, "transcribe", files=only or "all", model=llm.c["model"], backend=llm.c["backend"],
            prompt_sha=sha256_text(system or load_prompt(cfg, llm.c.get("prompt_file", "transcribe_plain")))[:12])


# ------------------------------------------------------------------ 3. segment
def merge_windows(windows: list[list[dict]]) -> list[dict]:
    """Combine per-window page labels. A window cannot judge its own first page (it sees nothing
    before it), so for every window after the first that label is taken from the previous window."""
    labels: dict[int, dict] = {}
    for i, win in enumerate(windows):
        first = min(l["page"] for l in win) if win else None
        for l in win:
            if i > 0 and l["page"] == first and l["page"] in labels:
                continue
            labels[l["page"]] = l
    return [labels[p] for p in sorted(labels)]


def labels_to_docs(labels: list[dict]) -> tuple[list[list[int]], list[str]]:
    """Page labels -> documents (lists of pages; pages need not be consecutive)."""
    docs: list[list[int]] = []
    page_doc: dict[int, int] = {}
    warnings, last = [], None
    for l in sorted(labels, key=lambda x: x["page"]):
        p, rel = l["page"], l["relation"]
        if rel == "blank":
            continue
        target = None
        if rel == "continues":
            target = last
        elif rel == "continues_earlier":
            target = page_doc.get(l.get("continues_page"))
            if target is None:
                warnings.append(f"page {p}: continues_earlier points to page {l.get('continues_page')}, which has no document")
        if target is None:
            docs.append([])
            target = len(docs) - 1
        docs[target].append(p)
        page_doc[p] = target
        last = target
    return docs, warnings


def _excerpt(t: str) -> str:
    return t if len(t) < 900 else t[:600] + "\n[...]\n" + t[-250:]


def segment_pairwise(cfg: Config, llm: LLM, f: dict, pages: list[dict]) -> list[dict]:
    """One decision per page: the page is shown next to the nearest preceding page that has content,
    and the model says whether it starts a new document. Slower than windows (one call per page),
    but a simpler question, which smaller models answer more reliably."""
    system, schema, sc = load_prompt(cfg, "segment_pair"), json_schema(PairLabel), cfg["segment"]
    px = sc.get("pair_image_max_px", 1024)
    jobs, prev = [], None
    for r in pages:
        jobs.append((r, prev))
        if not r.get("blank_candidate"):
            prev = r

    def work(job):
        r, prev = job
        if r.get("blank_candidate"):
            return {"page": r["page"], "relation": "blank", "continues_page": None, "note": None, "reason": "no text found by any OCR"}
        if prev is None:
            return {"page": r["page"], "relation": "new", "continues_page": None, "note": None, "reason": "first page with content"}
        user = (f"Folder: Rotary Club of {f['club']} ({f['country']}), {f['n_pages']} pages.\n"
                f"First image: PREVIOUS page (page {prev['page']}). Second image: CURRENT page (page {r['page']}).\n"
                f'<previous_page n="{prev["page"]}">\n{_excerpt(prev["text"])}\n</previous_page>\n'
                f'<current_page n="{r["page"]}">\n{_excerpt(r["text"])}\n</current_page>')
        res, _ = llm.call("segment_pair", system, user, [prev["image_path"], r["image_path"]], schema,
                          image_max_px=px, validate=PairLabel)
        res = PairLabel(**res).model_dump()
        return {"page": r["page"], "relation": res["relation"], "continues_page": None, "note": None, "reason": res["reason"]}

    FAIL_LABEL[0] = lambda job: f"{f['file_number']} p{job[0]['page']}"
    return [l for l in pmap(work, jobs, int(llm.c.get("workers", 1))) if l is not None]


def _same_heading(a: str | None, b: str | None) -> bool:
    import difflib
    na, nb = (re.sub(r"[^a-z0-9 ]", "", (x or "").lower()).strip() for x in (a, b))
    da, db = set(re.findall(r"\d+", na)), set(re.findall(r"\d+", nb))
    if da and da != db:   # "No. 55" after "No. 49", or a numbered masthead after a plain running head: a new item
        return False
    return bool(na) and bool(nb) and (na in nb or nb in na or difflib.SequenceMatcher(None, na, nb).ratio() >= 0.8)


def decide_from_facts(cur: dict, prev: dict | None) -> tuple[str, str]:
    """Document boundary from two page descriptions, by explicit rules checked in this order.
    Returns (relation, reason). `prev` is the nearest preceding page with content."""
    if cur.get("is_blank"):
        return "blank", "blank page"
    if prev is None:
        return "new", "first page with content"
    kind, pkind = cur.get("page_kind"), prev.get("page_kind")
    n, pn = cur.get("printed_page_number"), prev.get("printed_page_number")
    letter_start = cur.get("has_letterhead") or cur.get("has_salutation") or (kind == "letter" and cur.get("date_line"))
    # 1. signs that a new item starts, which outweigh everything else
    if kind == "letter" and letter_start and not cur.get("begins_mid_text"):
        return "new", "letter opening (letterhead, date line or salutation)"
    if n == 1 and (pn or 0) >= 1 and not cur.get("looks_like_reverse_side"):
        return "new", "page numbered 1"
    # 2. signs of continuation
    if cur.get("looks_like_reverse_side") and pkind in ("form", "certificate", "record_card") and not prev.get("looks_like_reverse_side"):
        return "continues", f"reverse side of the {pkind}"
    if n and pn and n == pn + 1 and kind == pkind:
        return "continues", f"page {n} follows page {pn}"
    if cur.get("begins_mid_text") and kind == pkind:
        return "continues", "begins in the middle of text from the previous page"
    if _same_heading(cur.get("heading"), prev.get("heading")) and kind == pkind and kind in ("bulletin", "roster_or_list", "regulations", "programme"):
        return "continues", "same running heading"
    # 3. otherwise the page starts something new if it is a different kind of page or has its own heading
    if kind != pkind:
        if cur.get("looks_like_reverse_side") and pkind in ("form", "certificate", "record_card"):
            return "continues", f"reverse side of the {pkind}"
        return "new", f"{kind} after {pkind}"
    if cur.get("heading"):
        return "new", f"own heading: {cur['heading'][:60]}"
    if kind in ("form", "certificate", "record_card", "cover_or_contents", "clipping", "photograph"):
        return "new", f"another {kind} (single-sheet items)"
    return "continues", f"same kind of page ({kind}), no sign of a new start"


def page_facts(cfg: Config, llm: LLM, f: dict, pages: list[dict]) -> dict[int, dict]:
    """One description per page (cached in data/work/pagefacts/). Pages without any OCR text are taken as blank."""
    system, schema = load_prompt(cfg, "page_facts"), json_schema(PageFacts)
    px = cfg["segment"].get("pair_image_max_px", 1024)
    path = cfg.work / "pagefacts" / f"{f['file_number']}.jsonl"
    sha = sha256_text(system)[:12]
    known = {r["page"]: r for r in read_jsonl(path) if r.get("prompt_sha") == sha and r.get("model") == llm.c["model"]}

    def work(r):
        if r["page"] in known:
            return known[r["page"]]
        if r.get("blank_candidate"):
            facts = PageFacts(is_blank=True).model_dump()
        else:
            user = (f"Folder: Rotary Club of {f['club']} ({f['country']}). Scan page {r['page']} of {f['n_pages']}.\n"
                    f"<ocr>\n{_excerpt(r['text'])}\n</ocr>")
            res, _ = llm.call("page_facts", system, user, [r["image_path"]], schema, image_max_px=px, validate=PageFacts)
            facts = PageFacts(**res).model_dump()
        return {"page": r["page"], "model": llm.c["model"], "prompt_sha": sha, **facts}

    FAIL_LABEL[0] = lambda r: f"{f['file_number']} p{r['page']}"
    rows = [x for x in pmap(work, pages, int(llm.c.get("workers", 1))) if x is not None]
    write_jsonl(path, sorted(rows, key=lambda x: x["page"]))
    return {x["page"]: x for x in rows}


def segment_features(cfg: Config, llm: LLM, f: dict, pages: list[dict]) -> list[dict]:
    """The model only describes each page; the boundaries then follow from fixed rules (decide_from_facts).
    Every decision can be traced to what was seen on the page and to the rule that applied."""
    facts = page_facts(cfg, llm, f, pages)
    labels, prev = [], None
    for r in pages:
        cur = facts.get(r["page"])
        if cur is None:
            labels.append({"page": r["page"], "relation": "new", "continues_page": None, "note": "page could not be described", "reason": None})
            continue
        relation, reason = decide_from_facts(cur, prev)
        seen = ", ".join(str(x) for x in (cur["page_kind"], cur.get("heading")) if x)
        labels.append({"page": r["page"], "relation": relation, "continues_page": None, "note": None,
                       "reason": f"{reason} [{seen}]"})
        if relation != "blank":
            prev = cur
    return labels


def segment(cfg: Config, only=None) -> None:
    llm, system = LLM(cfg, "segment"), load_prompt(cfg, "segment")
    schema, sc = json_schema(Segmentation), cfg["segment"]
    method = sc.get("method", "window")
    for f in load_files(cfg, only):
        fn = f["file_number"]
        pages = load_pages(cfg, fn)
        if method in ("pairwise", "features"):
            labels = (segment_pairwise if method == "pairwise" else segment_features)(cfg, llm, f, pages)
            missing = sorted({r["page"] for r in pages} - {l["page"] for l in labels})
            labels += [{"page": p, "relation": "new", "continues_page": None, "note": "not labelled by model"} for p in missing]
            write_json(cfg.work / "segments" / f"{fn}.json", {
                "file_number": fn, "method": f"llm-{method}", "model": llm.c["model"],
                "prompt_sha": sha256_text(load_prompt(cfg, "segment_pair" if method == "pairwise" else "page_facts"))[:12],
                "pages": sorted(labels, key=lambda x: x["page"])})
            print(f"segment: {fn} - {len(labels_to_docs(labels)[0])} documents ({method})")
            continue
        step, wins = sc["window"] - sc["overlap"], []
        for start in range(0, len(pages), step):
            win = pages[start:start + sc["window"]]
            if start > 0 and len(win) <= sc["overlap"]:
                break
            wins.append(win)

        def work(win, f=f):
            parts = [f"Folder: Rotary Club of {f['club']} ({f['country']}), {f['n_pages']} pages. "
                     f"The {len(win)} images are pages {win[0]['page']}-{win[-1]['page']}, in order.",
                     "OCR excerpts (start ... end of each page):"]
            for r in win:
                t = r["text"]
                excerpt = t if len(t) < 900 else t[:600] + "\n[...]\n" + t[-250:]
                parts.append(f'<page n="{r["page"]}">\n{excerpt}\n</page>')
            res, _ = llm.call("segment", system, "\n".join(parts), [r["image_path"] for r in win], schema,
                              image_max_px=sc["image_max_px"], validate=Segmentation)
            valid = {r["page"] for r in win}
            return [l for l in Segmentation(**res).model_dump()["pages"] if l["page"] in valid]

        FAIL_LABEL[0] = lambda win, fn=fn: f"{fn} pages {win[0]['page']}-{win[-1]['page']}"
        windows = [w for w in pmap(work, wins, int(llm.c.get("workers", 1))) if w is not None]
        labels = merge_windows(windows)
        missing = sorted({r["page"] for r in pages} - {l["page"] for l in labels})
        labels += [{"page": p, "relation": "new", "continues_page": None, "note": "not labelled by model"} for p in missing]
        write_json(cfg.work / "segments" / f"{fn}.json", {
            "file_number": fn, "method": "llm-window", "model": llm.c["model"],
            "prompt_sha": sha256_text(system)[:12], "window": sc["window"], "overlap": sc["overlap"],
            "pages": sorted(labels, key=lambda x: x["page"])})
        print(f"segment: {fn} - {len(labels_to_docs(labels)[0])} documents")
    report_failures(cfg, "segment")
    log_run(cfg, "segment", files=only or "all", model=llm.c["model"], backend=llm.c["backend"])


def final_labels(cfg: Config, fn: str) -> tuple[list[dict], dict]:
    """Model labels with human overrides from corrections/segments/<file_number>.yaml applied."""
    import yaml

    seg = read_json(cfg.work / "segments" / f"{fn}.json")
    if seg is None:
        return [], {}
    labels = {l["page"]: l for l in seg["pages"]}
    override = cfg.p("corrections_dir") / "segments" / f"{fn}.yaml"
    if override.exists():
        for l in (yaml.safe_load(override.read_text(encoding="utf-8")) or {}).get("pages", []):
            labels[l["page"]] = {"continues_page": None, "note": None, **l, "corrected": True}
    return [labels[p] for p in sorted(labels)], {k: v for k, v in seg.items() if k != "pages"}


# ------------------------------------------------------------------ 4. enrich
GENRE_DEFINITIONS = {
    "correspondence": "a letter, telegram or memo from one party to another",
    "roster": "a list of members, officers or committees of a club",
    "report": "a report or return, including filled-in forms such as the semi-annual report of club membership and directory data sheets",
    "news_clipping": "one or more cuttings from a newspaper or magazine",
    "regulations": "constitution, by-laws, rules",
    "pamphlet": "a programme, menu, invitation, brochure or other small printed piece for an occasion",
    "periodical": "an issue (or pages of an issue) of a club bulletin, newsletter or magazine",
    "certificate": "a charter, credential certificate, diploma or similar formal attestation",
    "administrative_record": "the archive's or secretariat's own housekeeping: binder cover sheets, tables of contents, index cards, ledger and record cards, address slips",
    "other": "none of the above",
}


def genre_definitions(cfg: Config) -> dict[str, str]:
    """config `genres` may be a plain list (built-in definitions are used) or a mapping name -> definition."""
    g = cfg["genres"]
    if isinstance(g, dict):
        return {k: (v or GENRE_DEFINITIONS.get(k, "")) for k, v in g.items()}
    return {k: GENRE_DEFINITIONS.get(k, "") for k in g}


def _text_chunks(doc_pages: list[int], pages: dict, size: int) -> list[str]:
    """The document's text in pieces of about `size` characters, cut at page ends where possible
    (a long page is cut at line ends), so that long rosters are read completely."""
    chunks, cur = [], ""
    for p in doc_pages:
        block = f'<page n="{p}">\n{pages[p]["text"]}\n</page>\n'
        while len(block) > size:   # a single very long page
            cut = block.rfind("\n", 0, size)
            cut = cut if cut > size // 2 else size
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(block[:cut])
            block = block[cut:]
        if cur and len(cur) + len(block) > size:
            chunks.append(cur)
            cur = ""
        cur += block
    if cur.strip():
        chunks.append(cur)
    return chunks


def merge_entities(parts: list[dict]) -> dict:
    """Union of the entity lists found in each piece of text; one entry per (type, name as written)."""
    out = {k: [] for k in Entities.model_fields}
    seen = set()
    for part in parts:
        for etype in out:
            for e in part.get(etype) or []:
                key = (etype, " ".join(e["name"].split()).casefold())
                if key not in seen:
                    seen.add(key)
                    out[etype].append(e)
    return out


def enrich(cfg: Config, only=None) -> None:
    """Two questions per document, each simple enough for a mid-sized local model:
    1. description (genre, title, dates, places, creators, recipients, languages, summary, tables) from images + text;
    2. named entities, from the text alone, read piece by piece so that nothing in a long list is skipped."""
    llm, llm_ent = LLM(cfg, "enrich"), LLM(cfg, "entities")
    system, system_ent = load_prompt(cfg, "enrich"), load_prompt(cfg, "entities")
    genres = genre_definitions(cfg)
    schema, schema_ent, ec = json_schema(EnrichCore, list(genres)), json_schema(Entities), cfg["enrich"]
    genre_block = "Genres:\n" + "\n".join(f"- {k}: {v}" for k, v in genres.items())
    ent_images = bool(ec.get("entities_with_images", False))
    for f in load_files(cfg, only):
        fn = f["file_number"]
        labels, _ = final_labels(cfg, fn)
        if not labels:
            print(f"enrich: {fn} has no segmentation yet - skipped")
            continue
        pages = {r["page"]: r for r in load_pages(cfg, fn)}
        facts = {r["page"]: r for r in read_jsonl(cfg.work / "pagefacts" / f"{fn}.jsonl")}
        out_path = cfg.work / "enriched" / f"{fn}.jsonl"
        previous = {r["doc_id"]: r for r in read_jsonl(out_path)}

        # Phase 1 for all documents of the folder, then phase 2: if the two phases use different models,
        # the server swaps models twice per folder and not twice per document.
        def describe(doc_pages, f=f, pages=pages, previous=previous, facts=facts):
            doc_id = doc_id_for(f["file_number"], doc_pages[0])
            old = previous.get(doc_id)
            if old and old["pages"] == doc_pages and old["provenance"].get("method") != "llm":
                return {"kept": old}  # human/gold annotation for unchanged pages: keep
            images = [pages[p]["image_path"] for p in doc_pages[:ec["max_images"]]]
            text = "\n\n".join(f'<page n="{p}">\n{pages[p]["text"]}\n</page>' for p in doc_pages)
            if len(text) > ec["max_chars"]:
                half = ec["max_chars"] // 2
                text = text[:half] + "\n[... middle omitted ...]\n" + text[-half:]
            seen = [f"p{p}: {facts[p]['page_kind']}" + (f", headed \"{facts[p]['heading']}\"" if facts[p].get("heading") else "")
                    for p in doc_pages[:6] if p in facts and not facts[p].get("is_blank")]
            user = (f"Folder context (from the archive index, not from the document): Rotary Club of {f['club']}, "
                    f"{f['country']}; chartered {f['chartered']}, terminated {f['terminated']}.\n"
                    f"This document = scan pages {doc_pages}. Images of the first {len(images)} page(s) are attached.\n"
                    + (f"What an earlier look at the pages noted: {'; '.join(seen)}.\n" if seen else "")
                    + f"\n{genre_block}\n\nText:\n{text}")
            core, meta = llm.call("enrich", system, user, images, schema, validate=EnrichCore)
            return {"doc_id": doc_id, "pages": doc_pages, "core": EnrichCore(**core).model_dump(), "meta": meta, "images": images}

        def entities(d, pages=pages):
            if d is None or "kept" in d:
                return d and d["kept"]
            parts = []
            for chunk in _text_chunks(d["pages"], pages, int(ec.get("entities_chunk_chars", 6000))):
                res, _ = llm_ent.call("entities", system_ent, f"Text:\n{chunk}", d["images"] if ent_images else [],
                                      schema_ent, validate=Entities)
                parts.append(Entities(**res).model_dump())
            record = Enrichment(**{**d["core"], "entities": merge_entities(parts)}).model_dump()
            return {"doc_id": d["doc_id"], "pages": d["pages"], "enrichment": record, "review_status": "unreviewed",
                    "provenance": {"method": "llm", **d["meta"], "entities_model": llm_ent.c["model"],
                                   "entities_prompt_sha": sha256_text(system_ent)[:12], "entities_calls": len(parts)}}

        FAIL_LABEL[0] = lambda d, fn=fn: f"{fn} document starting p{d[0]} (description)"
        described = pmap(describe, labels_to_docs(labels)[0], int(llm.c.get("workers", 1)))
        FAIL_LABEL[0] = lambda d, fn=fn: f"{fn} document {(d or {}).get('doc_id', '?')} (entities)"
        rows = [r for r in pmap(entities, described, int(llm_ent.c.get("workers", 1))) if r is not None]
        write_jsonl(out_path, rows)
        n_ent = sum(len(v) for r in rows for v in r["enrichment"]["entities"].values())
        n_per = sum(len(r["enrichment"]["entities"]["persons"]) for r in rows)
        print(f"enrich: {fn} - {len(rows)} documents, {n_ent} entities of which {n_per} persons")
    report_failures(cfg, "enrich")
    log_run(cfg, "enrich", files=only or "all", model=llm.c["model"], backend=llm.c["backend"], prompt_sha=sha256_text(system)[:12],
            entities_model=llm_ent.c["model"], entities_prompt_sha=sha256_text(system_ent)[:12])


# ------------------------------------------------------------------ reference annotations
def load_gold(cfg: Config, path: Path) -> None:
    """Install a hand-made annotation file (gold/*.yaml) as the segmentation + enrichment of a folder.
    Used for the worked example, for tests, and as the reference in `evaluate`."""
    import yaml

    g = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    fn, labels, rows = g["file_number"], [], []
    covered = set()
    for d in g["documents"]:
        pages = d["pages"]
        for i, p in enumerate(pages):
            rel = "new" if i == 0 else ("continues" if p == pages[i - 1] + 1 else "continues_earlier")
            labels.append({"page": p, "relation": rel,
                           "continues_page": pages[i - 1] if rel == "continues_earlier" else None, "note": None})
            covered.add(p)
        enr = Enrichment(**{k: v for k, v in d.items() if k != "pages"})
        rows.append({"doc_id": doc_id_for(fn, pages[0]), "pages": pages, "enrichment": enr.model_dump(),
                     "review_status": g.get("review_status", "unreviewed"),
                     "provenance": {"method": "manual", "annotator": g["annotator"], "date": str(g["date"]), "source": Path(path).name}})
    labels += [{"page": p, "relation": "blank", "continues_page": None, "note": None} for p in g.get("blank_pages", [])]
    write_json(cfg.work / "segments" / f"{fn}.json", {"file_number": fn, "method": "manual", "annotator": g["annotator"],
                                                      "pages": sorted(labels, key=lambda x: x["page"])})
    write_jsonl(cfg.work / "enriched" / f"{fn}.jsonl", rows)
    log_run(cfg, "load-gold", file=fn, source=str(path))
    print(f"load-gold: {fn} - {len(rows)} documents, {len(g.get('blank_pages', []))} blank pages")
