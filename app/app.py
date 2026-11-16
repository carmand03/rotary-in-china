"""RCTB viewer. Run from the project folder:  streamlit run app/app.py

Search by keyword / meaning, read page image and text side by side, show metadata and
entities on demand, and (with an LLM backend configured) ask questions with cited answers.
"""
import json
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from rctb.search import TextBase  # noqa: E402
from rctb.util import Config  # noqa: E402

st.set_page_config(page_title="Rotary in China Textbase", layout="wide")
import os

CONFIG = ROOT / os.environ.get("RCTB_CONFIG", "config.yaml")  # e.g. RCTB_CONFIG=config.local.yaml streamlit run app/app.py
cfg = Config(CONFIG)
RAG_ON = ({**cfg["llm"], **(cfg["llm"].get("stages") or {}).get("rag", {})})["backend"] != "none"


def val(v, default=""):
    """Empty database cells arrive as None or NaN: show a default instead."""
    return default if v is None or (isinstance(v, float) and v != v) else v


@st.cache_resource
def textbase(out_dir: str):
    return TextBase(out_dir, CONFIG)


def describe_run(path: Path) -> str:
    """One line on how a results folder was produced, read from the folder itself."""
    import json as _json
    import sqlite3
    bits = []
    try:
        con = sqlite3.connect(path / "rctb.sqlite")
        n = con.execute("SELECT count(*) FROM documents").fetchone()[0]
        bits.append(f"{n} documents")
        row = con.execute("SELECT provenance FROM documents LIMIT 1").fetchone()
        if row:
            prov = _json.loads(row[0])
            seg, enr = prov.get("segmentation", {}), prov.get("enrichment", {})
            bits.append(f"segmentation: {seg.get('method', '?')}" + (f" ({seg['model']})" if seg.get("model") else ""))
            bits.append(f"enrichment: {enr.get('model') or enr.get('method', '?')}")
        layers = dict(con.execute("SELECT text_layer, count(*) FROM pages GROUP BY 1").fetchall())
        bits.append("text: " + ", ".join(f"{k} {v}" for k, v in layers.items()))
        con.close()
        meta = path / "index" / "meta.json"
        if meta.exists():
            m = _json.loads(meta.read_text())
            bits.append(f"index: {m.get('model', m.get('backend'))}")
    except Exception as e:
        bits.append(f"(could not read details: {e})")
    return " · ".join(bits)


# Every folder next to the configured output folder that holds a database is offered as a run to open,
# e.g. data/out, data/out2, data/out_sample. Each browser tab keeps its own choice, for side-by-side comparison.
RUNS = sorted(p for p in cfg.out.parent.iterdir() if (p / "rctb.sqlite").exists()) if cfg.out.parent.exists() else []
if not RUNS:
    st.error(f"No database found under {cfg.out.parent}. Run the pipeline, or copy a results folder there.")
    st.stop()
with st.sidebar:
    st.header("Rotary in China Textbase")
    OUT = st.selectbox("Results to open", RUNS, index=RUNS.index(cfg.out) if cfg.out in RUNS else 0,
                       format_func=lambda p: p.name) if len(RUNS) > 1 else RUNS[0]
    st.caption(describe_run(OUT))

tb = textbase(str(OUT))
docs = tb.sql("SELECT doc_id, file_number, club, genre, title, date_produced, year, pages, n_pages FROM documents")

with st.sidebar:
    st.caption(f"{len(docs)} documents · {docs['file_number'].nunique()} folders")
    query = st.text_input("Search", placeholder="exchange rate · 哈爾濱 · \"family night\"")
    mode = st.radio("Mode", ["keyword", "semantic", "hybrid"], horizontal=True)
    k = st.slider("Results (k)", 1, 50, 10)
    clubs = st.multiselect("Club", sorted(docs["club"].dropna().unique()))
    folders = st.multiselect("Archive folder", sorted(docs["file_number"].dropna().unique()))
    genres = st.multiselect("Genre", sorted(docs["genre"].dropna().unique()))
    years = docs["year"].dropna()
    y0, y1 = (int(years.min()), int(years.max())) if len(years) else (1919, 1952)
    yr = st.slider("Year", y0, y1, (y0, y1)) if y0 < y1 else (y0, y1)
    use_year = st.checkbox("Filter by year (hides undated documents)", value=False)
    st.divider()
    ENTITY_TYPES = {"persons": "Persons", "organizations": "Organizations", "locations": "Locations",
                    "events": "Events", "works": "Works"}
    ent_type = st.selectbox("Named entity: type", [None, *ENTITY_TYPES], format_func=lambda t: ENTITY_TYPES.get(t, "(no entity filter)"))
    ent_names, ent_all = [], False
    if ent_type:
        # variants are grouped under their canonical form (corrections/entities.csv)
        names = tb.sql("SELECT canonical AS label, count(DISTINCT doc_id) AS n FROM entities "
                       "WHERE type = ? GROUP BY label ORDER BY n DESC, label", [ent_type])
        counts = dict(zip(names["label"], names["n"]))
        ent_names = st.multiselect(f"Named entity: names ({len(counts)})", list(counts), format_func=lambda x: f"{x} ({counts[x]})")
        if len(ent_names) > 1:
            ent_all = st.radio("Documents mentioning", ["any of them", "all of them"], horizontal=True) == "all of them"
    st.divider()
    show_enrich = st.checkbox("Show metadata and entities", value=True)

filters = {"club": clubs, "genre": genres}
if folders:   # restrict everything (browsing, search, RAG) to the chosen folders
    in_folders = docs.loc[docs["file_number"].isin(folders), "doc_id"].tolist()
    filters["doc_id"] = in_folders or ["(none)"]
if use_year:
    filters.update(year_from=yr[0], year_to=yr[1])
entity_docs = None
if ent_names:
    marks = ",".join("?" * len(ent_names))
    entity_docs = tb.sql(f"SELECT doc_id FROM entities WHERE type = ? AND canonical IN ({marks}) "
                         f"GROUP BY doc_id HAVING count(DISTINCT canonical) >= ?",
                         [ent_type, *ent_names, len(ent_names) if ent_all else 1])["doc_id"].tolist()
    if folders:
        entity_docs = [d for d in entity_docs if d in set(in_folders)]
    filters["doc_id"] = entity_docs or ["(none)"]

tab_browse, tab_ask = st.tabs(["Search and read", "Ask (RAG)"])

with tab_browse:
    if query:
        try:
            hits = getattr(tb, mode)(query, k=k, **filters)
        except OSError as e:   # the embedding server (Ollama) is not reachable from this machine
            st.warning(f"Semantic search is unavailable ({e}). Showing keyword results. "
                       "Start Ollama and pull the embedding model named in data/out/index/meta.json to enable it.")
            hits = tb.keyword(query, k=k, **filters)
        if tb.last_warning:
            st.warning(tb.last_warning)
            tb.last_warning = None
    else:
        hits = docs
        if clubs:
            hits = hits[hits["club"].isin(clubs)]
        if folders:
            hits = hits[hits["file_number"].isin(folders)]
        if genres:
            hits = hits[hits["genre"].isin(genres)]
        if use_year:
            hits = hits[hits["year"].between(*yr)]
        if entity_docs is not None:
            hits = hits[hits["doc_id"].isin(entity_docs)]
    if hits.empty:
        st.info("No documents match.")
        st.stop()
    hits = hits.drop_duplicates("doc_id")
    label = lambda i: (f"{hits.loc[i, 'doc_id']} · {val(hits.loc[i, 'date_produced'], 'n.d.')} · "
                       f"{val(hits.loc[i, 'genre'], '?')} · {str(val(hits.loc[i, 'title'], '(no title)'))[:70]}")
    choice = st.selectbox(f"{len(hits)} documents", hits.index, format_func=label)
    doc_id = hits.loc[choice, "doc_id"]
    if query and "snippet" in hits:
        st.caption(str(val(hits.loc[choice, "snippet"])))
    doc = tb.sql("SELECT * FROM documents WHERE doc_id = ?", [doc_id]).iloc[0]
    pages = tb.sql("SELECT * FROM pages WHERE doc_id = ? ORDER BY page", [doc_id])

    st.subheader(val(doc["title"], doc_id))
    st.caption(f"{doc['club']} · {doc['file_number']} pp. {doc['start_page']}-{doc['end_page']} · {doc['genre']} · "
               f"{val(doc['date_produced'], 'undated')} · {doc['review_status']}")
    page = st.select_slider("Page", list(pages["page"])) if len(pages) > 1 else pages["page"].iloc[0]
    prow = pages[pages["page"] == page].iloc[0]
    left, right = st.columns(2)
    with left:
        # the page image: in the results folder opened, else in any other results folder (images are the same for every run)
        img = next((r / prow["image"] for r in [OUT, *RUNS] if val(prow["image"], None) and (r / prow["image"]).exists()), None)
        if img:
            st.image(str(img), width="stretch")
        else:
            st.warning(f"No image for {doc['file_number']} page {page} on this computer "
                       f"(expected: {OUT / str(val(prow['image'], 'images/...'))}). The text is shown on the right.")
    with right:
        layers = {"reference text": prow["text"], "Google Vision (your files)": prow["text_provided"],
                  "Tesseract": prow["text_tesseract"], "PDF text layer": prow["text_embedded"]}
        layers = {n: val(t, None) for n, t in layers.items()}
        which = st.radio("Text layer", [n for n, t in layers.items() if t is not None], horizontal=True,
                         help=f"The reference text of this page comes from the '{prow['text_layer']}' layer.")
        st.text_area("text", layers[which] or "", height=700, label_visibility="collapsed")
        if prow["text_layer"] == "llm" and val(prow.get("llm_draft"), None):
            start = prow["llm_draft"].split(",")[0]
            pct = lambda x: "?" if val(x, None) is None else f"{x:.0%}"
            with st.expander(f"What the vision model changed (started from: {prow['llm_draft']}; kept {pct(prow['llm_kept'])} "
                             f"of its words, {pct(prow['llm_new'])} of the result is new)"):
                import difflib, html
                a, b = str(val(prow[f"text_{start}"])).split(), str(val(prow["text"])).split()
                out = []
                for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
                    if tag in ("delete", "replace"):
                        out.append(f"<del style='color:#b00020'>{html.escape(' '.join(a[i1:i2]))}</del>")
                    if tag in ("insert", "replace"):
                        out.append(f"<ins style='color:#00695c'>{html.escape(' '.join(b[j1:j2]))}</ins>")
                    if tag == "equal":
                        out.append(html.escape(" ".join(b[j1:j2])))
                st.markdown(" ".join(out), unsafe_allow_html=True)
                st.caption("Red, struck through: in the OCR but removed or moved. Green: added or moved here by the model.")

    if show_enrich:
        st.markdown(f"**Summary.** {val(doc['summary'], '(none)')}")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Metadata**")
            st.dataframe(doc[["genre", "title_status", "date_produced", "date_received", "date_modified", "date_status",
                              "place_origin", "place_destination", "creators", "recipients", "languages",
                              "has_table", "has_illustration", "notes"]].astype(str).rename("value"), width="stretch")
            st.markdown("**All dates**")
            st.dataframe(tb.sql("SELECT type, value, as_written, certainty FROM dates WHERE doc_id = ?", [doc_id]),
                         hide_index=True, width="stretch")
        with c2:
            st.markdown("**Named entities** (name = as written; canonical = grouped form; grounded = found literally in the text)")
            st.dataframe(tb.sql("SELECT type, name, canonical, role, grounded FROM entities WHERE doc_id = ?", [doc_id]),
                         hide_index=True, width="stretch", height=420)
        for t in tb.sql("SELECT page, caption, markdown FROM tables_md WHERE doc_id = ?", [doc_id]).to_dict("records"):
            st.markdown(f"**Table, p. {t['page']}** {val(t['caption'])}\n\n{t['markdown']}")
        with st.expander("Provenance"):
            st.json(json.loads(doc["provenance"]))

with tab_ask:
    if not RAG_ON:
        st.info("Set `llm.backend` in config.yaml to enable answers. Retrieval below works without it.")
    q = st.text_input("Question (English or Chinese)", key="q")
    c1, c2 = st.columns(2)
    lang = c1.radio("Answer in", ["en", "zh"], horizontal=True, format_func={"en": "English", "zh": "中文"}.get)
    kk = c2.slider("Documents retrieved (k)", 1, 20, cfg["rag"]["top_k"])
    if q:
        if not RAG_ON:
            st.dataframe(tb.hybrid(q, k=kk, **filters)[["doc_id", "club", "date_produced", "pages", "text"]], hide_index=True)
        else:
            r = tb.ask(q, k=kk, language=lang, **filters)
            st.markdown(r["answer"])
            for c in r["citations"]:
                mark = "✅" if c["verified"] else "⚠️ quote not found verbatim"
                st.markdown(f"- **[{c['source']}]** `{c['doc_id']}` pp. {c['pages']} {mark}: “{c['quote']}”")
            with st.expander("Retrieved passages"):
                for s in r["sources"]:
                    st.markdown(f"**{s['label']}** `{s['doc_id']}` · {s['club']} · {s['date_produced']} · pp. {s['pages']}")
                    st.text(s["text"])
