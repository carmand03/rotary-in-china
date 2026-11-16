"""Stage `web`: articles harvested from rotaryinchina.org -> statements about persons, clubs and events.

    python scripts/harvest_rotaryinchina.py          the pages and their PDFs  (actors/web/rotaryinchina/)
    python -m rcad web [--limit 5] [--pages word]    statements, each with the passage that supports it
    python -m rcad build                             adds them to the database, labelled as secondary

The article proper is the PDF attached to each page; the page itself only carries a short summary in three
languages. Statements are therefore read from the PDF text (PDF page numbers are kept), and from the English
summary only when a page has no PDF. A model fills a fixed form for each passage (prompts/web_statements.md);
the code then checks that every quoted passage really is in the text, and sets aside the statements whose
quote is not found. Calls are cached: a second run only reads pages that are new or have changed.

Files (actors/work/):   web_pages.csv        every harvested page: kind, in scope or not and why, where its text comes from
                        web_statements.csv   the verified statements
                        web_rejected.csv     statements set aside, with the reason
Yours (actors/authority/web_pages.csv): `use` = yes / no overrides the rule that decides which pages are read.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import BaseModel, field_validator, model_validator

from rctb.llm import LLM, LLMUnavailable
from rctb.models import json_schema
from rctb.util import Config, read_jsonl

from .clubs import parse_date
from .resolve import read_csv_any, s


def require_all(node):
    """Schema in which every field must be filled in (with "" where nothing applies): small models skip optional fields.
    Defined here so that this stage also runs with an earlier version of the rctb package."""
    if isinstance(node, dict):
        node = {k: require_all(v) for k, v in node.items()}
        if isinstance(node.get("properties"), dict):
            node["required"] = list(node["properties"])
        return node
    return [require_all(v) for v in node] if isinstance(node, list) else node

ATTRIBUTES = ["birth", "death", "other_name", "nationality", "education", "occupation", "rotary_membership", "rotary_office", "honour",
              "relation", "residence", "organized", "admitted", "terminated", "charter_number", "meeting_place", "language", "sponsor",
              "member_count", "publication", "project", "held", "participant", "other"]
TYPES = ["person", "club", "institution", "event"]
ST_COLS = ["statement_id", "source_id", "url", "pdf_page", "subject", "subject_vernacular", "subject_type", "attribute", "value",
           "value_vernacular", "detail", "date_from", "date_to", "place", "quote", "text_from", "model"]
CJK = re.compile(r"[㐀-鿿]")
YEAR = re.compile(r"\b(19[12]\d|193\d|194\d|195[0-2])\b")


class Statement(BaseModel):
    subject: str
    subject_vernacular: str = ""
    subject_type: Literal["person", "club", "institution", "event"] = "person"
    attribute: str = "other"
    value: str = ""
    value_vernacular: str = ""
    detail: str = ""
    date_from: str = ""
    date_to: str = ""
    place: str = ""
    quote: str = ""

    @model_validator(mode="before")
    @classmethod
    def tolerant(cls, d):
        if isinstance(d, dict):
            d = {k: ("" if v is None else str(v) if not isinstance(v, str) else v) for k, v in d.items()}
            if d.get("subject_type") not in TYPES:
                d["subject_type"] = "person"
            if d.get("attribute") not in ATTRIBUTES:
                d["detail"] = "; ".join(x for x in (d.get("attribute", ""), d.get("detail", "")) if x)
                d["attribute"] = "other"
        return d

    @field_validator("subject")
    @classmethod
    def named(cls, v):
        if not v.strip():
            raise ValueError("a statement needs a subject")
        return v.strip()


class Statements(BaseModel):
    statements: list[Statement]


def squash(t: str) -> str:
    """Text without whitespace and with plain quotes/dashes: what 'the same passage' means when lines are broken differently."""
    t = t.translate(str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "}))
    return re.sub(r"\s+", "", t).casefold()


def source_id(url: str) -> str:
    return "W-" + hashlib.sha1(url.encode()).hexdigest()[:10]


def pdf_pages(path: Path, cache: Path | None = None) -> list[str]:
    """Text of each page of a PDF. Kept in actors/work/web_text/ so that each PDF is opened only once."""
    import json
    import logging
    logging.getLogger("pypdf").setLevel(logging.ERROR)       # pypdf reports every small irregularity of a file; they do not matter here
    memo = cache / (hashlib.sha1(f"{path.name}|{path.stat().st_size}".encode()).hexdigest()[:16] + ".json") if cache else None
    if memo and memo.exists():
        return json.loads(memo.read_text(encoding="utf-8"))
    with path.open("rb") as f:
        if not f.read(1024).lstrip().startswith(b"%PDF"):    # the site answered with a web page in place of the file
            print(f"       not a PDF (the download returned a web page): {path.name}")
            return []
    from pypdf import PdfReader
    try:
        pages = [re.sub(r"[ \t]+\n", "\n", (p.extract_text() or "")).strip() for p in PdfReader(str(path)).pages]
    except Exception as e:   # a damaged file must not stop the run
        print(f"       cannot read {path.name}: {type(e).__name__}")
        return []
    if memo:
        memo.parent.mkdir(parents=True, exist_ok=True)
        memo.write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
    return pages


def summary_en(page_text: str) -> str:
    """The English summary of a harvested page: the lines between 'ENG' and 'PTG'."""
    m = re.search(r"^ENG\s*\n(.*?)(?=^\s*(PTG|中文|Skip to PDF content|Related Posts:)\s*$)", page_text, flags=re.S | re.M)
    return m.group(1).strip() if m else ""


def read_harvest(web: Path, clubs: list[str], auth: Path, cache: Path | None = None) -> list[dict]:
    """One record per harvested page, with its text (PDF pages, else the English summary) and whether it will be read."""
    man = read_csv_any(web / "manifest.csv")
    if man.empty:
        raise SystemExit(f"{web / 'manifest.csv'} not found or empty: run scripts/harvest_rotaryinchina.py first.")
    files = {r["url"]: r for r in read_csv_any(web / "files.csv").fillna("").to_dict("records")}
    mine = {r["url"]: s(r.get("use")).strip().lower() for r in read_csv_any(auth / "web_pages.csv").to_dict("records")}
    club_re = re.compile(r"\b(" + "|".join(sorted(map(re.escape, clubs), key=len, reverse=True)) + r")\b", re.I) if clubs else None
    out = []
    for r in man.fillna("").to_dict("records"):
        if s(r["status"]) != "200" or not s(r["text_path"]):
            continue
        ptext = (web / r["text_path"]).read_text(encoding="utf-8") if (web / r["text_path"]).exists() else ""
        pdfs = [files[u] for u in s(r["files"]).split("; ") if u.lower().endswith(".pdf") and files.get(u, {}).get("local_path")
                and (web / files[u]["local_path"]).exists()]
        parts, text_from, pdf_url = [], "summary", ""
        if pdfs:
            parts = [(i + 1, t) for i, t in enumerate(pdf_pages(web / pdfs[0]["local_path"], cache)) if t]
            text_from, pdf_url = ("pdf", pdfs[0]["url"]) if parts else ("summary", "")
        if not parts:
            parts = [(0, summary_en(ptext))] if summary_en(ptext) else []
        full = r["title"] + "\n" + summary_en(ptext) + "\n" + "\n".join(t for _, t in parts)
        cats = f"{r['categories']}; {r['tags']}"
        kind = "section" if r["kind"] == "page" else "biography" if re.search(r"eminent|governor", cats, re.I) else "article"
        named = sorted({m.group(1).title() for m in club_re.finditer(full)}) if club_re else []
        n_years = len(YEAR.findall(full))
        rule = bool(parts) and kind != "section" and bool(named) and n_years >= 2
        why = ("section page (a list of links)" if kind == "section" else "no text" if not parts else
               f"names {', '.join(named[:4])}; {n_years} dates in 1919-1952" if rule else
               "names no club of the archive" if not named else "fewer than two dates in 1919-1952")
        use = mine.get(r["url"], "")
        out.append({"source_id": source_id(r["url"]), "url": r["url"], "title": r["title"], "kind": kind, "published": r["published"][:10],
                    "fetched_at": r["fetched_at"], "categories": cats.strip("; "), "text_from": text_from, "pdf_url": pdf_url,
                    "n_chars": sum(len(t) for _, t in parts), "in_scope": (use == "yes") or (rule and use != "no"),
                    "why": ("your choice: " + use) if use in ("yes", "no") else why, "use": use if use in ("yes", "no") else "", "parts": parts})
    return out


def chunks(parts: list[tuple[int, str]], size: int) -> list[str]:
    """Passages of about `size` characters (half that for Chinese text), cut at paragraph or sentence ends."""
    out, cur = [], ""
    for _, t in parts:
        lim = size // 2 if len(CJK.findall(t)) > len(t) / 4 else size
        for para in re.split(r"\n\s*\n", t):
            pieces = [para] if len(para) <= lim else re.findall(rf".{{1,{lim}}}(?:[.。！？!?]\s*|\n|$)", para, flags=re.S) or [para]
            for p in pieces:
                if cur and len(cur) + len(p) > lim:
                    out.append(cur)
                    cur = ""
                cur += ("\n\n" if cur else "") + p.strip()
    return out + ([cur] if cur.strip() else [])


def iso(v: str) -> str:
    v = s(v).strip()
    return v if re.fullmatch(r"\d{4}(-\d{2}){0,2}", v) else (parse_date(v) or "")


def run(base: Path, cfg: Config, limit: int = 0, only: list[str] | None = None) -> None:
    web, work, auth = base / "web" / "rotaryinchina", base / "work", base / "authority"
    work.mkdir(parents=True, exist_ok=True)
    clubs = sorted({re.sub(r"\s+club roster$", "", f["club"], flags=re.I) for f in read_jsonl(cfg.work / "files.jsonl")} | {"Hsinking", "Peiping"})
    print("web: reading the harvested pages and their PDFs (the first time takes a few minutes) ...", flush=True)
    pages = read_harvest(web, clubs, auth, work / "web_text")
    cols = ["url", "title", "kind", "in_scope", "why", "use", "text_from", "n_chars", "published", "categories", "pdf_url", "fetched_at", "source_id"]
    pd.DataFrame(pages, columns=cols).to_csv(work / "web_pages.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(pages, columns=["url", "title", "kind", "in_scope", "why", "use"]).to_csv(auth / "web_pages.csv", index=False, encoding="utf-8-sig")
    todo = [p for p in pages if p["in_scope"] and (not only or any(w.lower() in (p["url"] + p["title"]).lower() for w in only))]
    todo = todo[:limit] if limit else todo
    print(f"web: {len(pages)} pages harvested, {sum(p['in_scope'] for p in pages)} in scope "
          f"({sum(p['text_from'] == 'pdf' for p in pages if p['in_scope'])} with their PDF); reading {len(todo)}")

    llm = LLM(cfg, "web")
    system = (cfg.base / "prompts" / "web_statements.md").read_text(encoding="utf-8")
    schema = require_all(json_schema(Statements))
    size = int(cfg["llm"].get("stages", {}).get("web", {}).get("chunk_chars", 5000))
    keep_old = {"ok": read_csv_any(work / "web_statements.csv"), "no": read_csv_any(work / "web_rejected.csv")}
    done_ids = {p["source_id"] for p in todo}
    ok = [r for r in keep_old["ok"].fillna("").to_dict("records") if r.get("source_id") not in done_ids] if len(keep_old["ok"]) else []
    rejected = [r for r in keep_old["no"].fillna("").to_dict("records") if r.get("source_id") not in done_ids] if len(keep_old["no"]) else []
    failed = 0
    for n, p in enumerate(todo, 1):
        squashed = [(no, squash(t)) for no, t in p["parts"]]
        whole = "".join(t for _, t in squashed)
        seen = set()
        for i, chunk in enumerate(chunks(p["parts"], size)):
            user = f"Article: {p['title']}\n\nText (passage {i + 1}):\n\n{chunk}"
            try:
                try:
                    res, meta = llm.call("web_statements", system, user, [], schema, validate=Statements)
                except TypeError:      # earlier version of the rctb package: no `validate` argument
                    res, meta = llm.call("web_statements", system, user, [], schema)
            except LLMUnavailable:
                raise SystemExit("web: no model configured (llm.backend is 'none'). Use --config with a file that names one.")
            except Exception as e:
                failed += 1
                print(f"       FAILED {p['title'][:60]} passage {i + 1}: {type(e).__name__}: {str(e)[:160]}")
                continue
            for st in Statements.model_validate(res).statements:
                d = st.model_dump()
                q = squash(d["quote"])
                key = (squash(d["subject"]), d["attribute"], squash(d["value"]), d["date_from"])
                row = {**d, "date_from": iso(d["date_from"]), "date_to": iso(d["date_to"]), "source_id": p["source_id"], "url": p["url"],
                       "text_from": p["text_from"], "model": meta.get("model", llm.c["model"]),
                       "pdf_page": next((no for no, t in squashed if q and q in t), 0)}
                reason = ("no quote" if len(q) < 8 else "quote not found in the text" if q not in whole else
                          "subject not named in the quote or near it" if d["subject_type"] == "person" and not _near(d, whole, q) else
                          "repeated" if key in seen else "")
                seen.add(key)
                (rejected if reason else ok).append({**row, "reason": reason} if reason else row)
        print(f"  {n}/{len(todo)} {p['title'][:70]}", flush=True)
    count: dict[str, int] = {}
    for r in ok:                                   # number the statements within each page
        count[r["source_id"]] = count.get(r["source_id"], 0) + 1
        r["statement_id"] = f"{r['source_id']}-{count[r['source_id']]:03d}"
    pd.DataFrame(ok, columns=ST_COLS).to_csv(work / "web_statements.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(rejected, columns=ST_COLS[1:] + ["reason"]).to_csv(work / "web_rejected.csv", index=False, encoding="utf-8-sig")
    by = pd.Series([r["attribute"] for r in ok]).value_counts().head(8).to_dict() if ok else {}
    print(f"web: {len(ok)} statements kept, {len(rejected)} set aside (see web_rejected.csv)"
          + (f", {failed} passages failed (run again to retry)" if failed else "") + f"\n       most frequent: {by}\n       -> {work / 'web_statements.csv'}")


def _near(d: dict, whole: str, q: str) -> bool:
    """A statement about a person is kept only if that person is named in the quote or within about 600 characters before it:
    it catches facts attached to the wrong person."""
    names = [squash(x) for x in (d["subject_vernacular"], d["subject"].split(",")[0].split()[-1] if d["subject"].split() else "",
                                 d["subject"].split()[0] if d["subject"].split() else "") if len(squash(x)) >= 2]
    at = whole.find(q)
    window = whole[max(0, at - 600): at + len(q)]
    return any(n in window for n in names)


# ---------------------------------------------------------------- used by `build`
SUBJ_COLS = ["subject_id", "type", "name", "vernacular", "n_statements", "n_pages", "match_id", "status", "rule"]


def club_key(name: str) -> str:
    """'Canton Rotary Club', 'Rotary Club of Shanghai West', 'Shanghai West Rotary Club (滬西扶輪社)' -> 'canton', 'shanghai west'."""
    from rctb.normalize import fold
    n = re.sub(r"\(.*?\)", " ", name)
    return re.sub(r"\b(the|rotary|club|of)\b", " ", fold(n)).strip().replace("  ", " ")


def link(base: Path, clubs: list[dict], person_names: pd.DataFrame) -> tuple[list[dict], list[dict], list[dict]]:
    """(pages, subjects, statements) for the database. Subjects (the persons, clubs... the statements are about) are matched
    to the clubs and persons already in the database: clubs by name; persons by identical name in characters (auto), or by
    family name + compatible given names (suggested: applied only once you set the row to `confirmed` in
    actors/authority/web_subjects.csv). Subject identifiers are kept in that file for good."""
    from rctb.normalize import fold, given_compatible, parse_person
    work, auth = base / "work", base / "authority"
    st = read_csv_any(work / "web_statements.csv")
    pages = read_csv_any(work / "web_pages.csv")
    if st.empty or pages.empty:
        return [], [], []
    st, pages = st.fillna("").astype(str), pages.fillna("").astype(str)
    # one subject = one name; two spellings given with the same characters (George A. Fitch / George Ashmore Fitch 費吳生) are one subject
    by_vern: dict[tuple, str] = {}
    for r in st.to_dict("records"):
        if r["subject_vernacular"].strip() and r["subject_type"] != "club":
            by_vern.setdefault((r["subject_type"], r["subject_vernacular"].strip()), fold(r["subject"]))
    alias = {(typ, k): k for (typ, _), k in by_vern.items()}
    for r in st.to_dict("records"):
        v = (r["subject_type"], r["subject_vernacular"].strip())
        if v in by_vern:
            alias[(r["subject_type"], fold(r["subject"]))] = by_vern[v]
    key = lambda r: (r["subject_type"], club_key(r["subject"]) if r["subject_type"] == "club"
                     else alias.get((r["subject_type"], fold(r["subject"])), fold(r["subject"])))
    st["_key"] = [key(r) for r in st.to_dict("records")]
    old = read_csv_any(auth / "web_subjects.csv")
    old = old.fillna("").astype(str) if len(old) else pd.DataFrame(columns=SUBJ_COLS + ["key"])
    known = {(r["type"], r["key"]): r for r in old.to_dict("records")}
    nxt = max([int(r["subject_id"][1:]) for r in known.values() if re.fullmatch(r"W\d+", r["subject_id"])], default=0)
    by_name = {}
    for c in clubs:
        by_name.setdefault(fold(c["name"]), []).append(c["club_id"])
    pn = person_names.fillna("").astype(str).to_dict("records") if len(person_names) else []
    subjects = []
    for (typ, k), g in st.groupby("_key"):
        prev = known.get((typ, k), {})
        nxt += 0 if prev else 1
        name, vern = g["subject"].mode().iat[0], next((v for v in g["subject_vernacular"] if v), "")
        match, status, rule = "", "", ""
        if typ == "club":
            ids = by_name.get(k, [])
            match, status, rule = (ids[0], "auto", "same club name") if len(ids) == 1 else ("", "", "several clubs of that name" if ids else "")
        elif typ == "person" and pn:
            same = {r["person_id"] for r in pn if vern and r["vernacular_name"] == vern}
            fam, giv = parse_person(name)
            alike = {r["person_id"] for r in pn if fold(r["surname"]) == fam and given_compatible(giv, fold(r["given"]).split())}
            if len(same) == 1:
                match, status, rule = same.pop(), "auto", "same name in characters"
            elif len(alike) == 1:
                match, status, rule = alike.pop(), "suggested", "same family name, compatible given names"
        if prev.get("status") in ("confirmed", "rejected"):      # your decision stands
            match, status, rule = prev["match_id"], prev["status"], prev["rule"]
        subjects.append({"subject_id": prev.get("subject_id") or f"W{nxt:05d}", "type": typ, "name": name, "vernacular": vern,
                         "n_statements": len(g), "n_pages": g["source_id"].nunique(), "match_id": match, "status": status, "rule": rule, "key": k})
    gone = [r for kk, r in known.items() if kk not in {(x["type"], x["key"]) for x in subjects}]       # keep identifiers of subjects not seen this time
    pd.DataFrame(subjects + gone, columns=SUBJ_COLS + ["key"]).sort_values("subject_id").to_csv(auth / "web_subjects.csv", index=False, encoding="utf-8-sig")
    sid = {(x["type"], x["key"]): x for x in subjects}
    n_st = st.groupby("source_id").size()
    T_pages = [{**{c: r[c] for c in ("source_id", "url", "title", "kind", "published", "fetched_at", "text_from", "pdf_url")},
                "n_statements": int(n_st.get(r["source_id"], 0))} for r in pages.to_dict("records") if r["source_id"] in n_st.index]
    T_st = []
    for r in st.to_dict("records"):
        x = sid[r["_key"]]
        ok = x["status"] in ("auto", "confirmed") and x["match_id"]
        T_st.append({**{c: (r[c] or None) for c in ST_COLS if c not in ("url", "text_from")}, "pdf_page": int(float(r["pdf_page"] or 0)) or None,
                     "subject_id": x["subject_id"], "person_id": x["match_id"] if ok and x["type"] == "person" else None,
                     "club_id": x["match_id"] if ok and x["type"] == "club" else None, "reliability": "secondary"})
    return T_pages, [{c: (x[c] or None) for c in SUBJ_COLS} for x in subjects], T_st
