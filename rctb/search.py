"""Query layer: load the database as dataframes; keyword, semantic and hybrid search; RAG.

    from rctb.search import TextBase
    tb = TextBase("data/out")
    tb.documents            # pandas DataFrame, one row per document
    tb.keyword("exchange rate", k=10, genre="correspondence")
    tb.semantic("members criticised for poor attendance", k=5)
    tb.ask("Who was president of the Harbin club in 1937?", k=5, language="zh")
"""
from __future__ import annotations

import json
import pickle
import re
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from .util import Config, log_run, norm_ws, now, write_json

CJK = re.compile(r"[぀-ヿ㐀-鿿]")


# ------------------------------------------------------------------ embeddings (stage 6)
def chunk_document(doc: dict, size: int, overlap: int) -> list[dict]:
    """Overlapping passages; each remembers its character span and the PDF pages it covers."""
    text, offsets = doc["full_text"], sorted((v, int(k)) for k, v in json.loads(doc["page_offsets"]).items())
    chunks, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            ws = max(text.rfind("\n", start + size // 2, end), text.rfind(" ", start + size // 2, end))
            end = ws if ws > start else end
        pages = [p for i, (off, p) in enumerate(offsets)
                 if off < end and (offsets[i + 1][0] if i + 1 < len(offsets) else len(text) + 1) > start]
        if text[start:end].strip():
            chunks.append({"chunk_id": f"{doc['doc_id']}#{len(chunks)}", "doc_id": doc["doc_id"], "char_start": start,
                           "char_end": end, "pages": json.dumps(pages), "text": text[start:end]})
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks


def embed(cfg: Config) -> None:
    ec, out = cfg["embedding"], cfg.out / "index"
    out.mkdir(parents=True, exist_ok=True)
    docs = pd.read_parquet(cfg.out / "tables" / "documents.parquet")
    chunks = pd.DataFrame([c for d in docs.to_dict("records") for c in chunk_document(d, ec["chunk_chars"], ec["chunk_overlap"])])
    if chunks.empty:
        print("embed: no documents yet - nothing to index")
        return
    chunks.to_parquet(out / "chunks.parquet", index=False)
    meta = {"backend": ec["backend"], "n_chunks": len(chunks), "time": now(), "chunk_chars": ec["chunk_chars"],
            "chunk_overlap": ec["chunk_overlap"]}
    if ec["backend"] == "sentence_transformers":
        from sentence_transformers import SentenceTransformer  # pip install sentence-transformers

        model = SentenceTransformer(ec["model"])
        vecs = model.encode(chunks["text"].tolist(), normalize_embeddings=True, show_progress_bar=True, batch_size=16)
        np.save(out / "embeddings.npy", np.asarray(vecs, dtype="float32"))
        meta["model"] = ec["model"]
    elif ec["backend"] == "ollama":  # an embedding model served by Ollama (e.g. bge-m3): no extra Python packages
        model = ec.get("ollama_model", "bge-m3")
        texts = chunks["text"].tolist()
        try:
            vecs = np.vstack([ollama_embed(model, texts[i:i + 16]) for i in range(0, len(texts), 16)])
        except Exception as e:  # model not pulled, server not running...: keep the run going with the baseline index
            print(f"embed: Ollama embedding with '{model}' failed ({str(e)[:160]}). In a terminal: ollama pull {model}. "
                  f"Using the tfidf baseline for now; run `embed` again afterwards.")
            ec = {**ec, "backend": "tfidf"}
            meta["backend"] = "tfidf"
        else:
            np.save(out / "embeddings.npy", vecs)
            meta["model"] = model
    if ec["backend"] in ("sentence_transformers", "ollama"):
        pass
    else:  # tfidf: character n-grams - no download, works across scripts, but lexical rather than semantic
        from sklearn.feature_extraction.text import TfidfVectorizer

        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True, min_df=1, lowercase=True)
        mat = vec.fit_transform(chunks["text"])
        with open(out / "tfidf.pkl", "wb") as fh:
            pickle.dump({"vectorizer": vec, "matrix": mat}, fh)
        meta["model"] = "tfidf char_wb 2-4"
    write_json(out / "meta.json", meta)
    log_run(cfg, "embed", **meta)
    print(f"embed: {len(chunks)} passages, backend {meta['backend']} ({meta['model']})")


def ollama_embed(model: str, texts: list[str]) -> np.ndarray:
    """Unit-length embedding vectors from the Ollama server at $OLLAMA_HOST (default 127.0.0.1:11434)."""
    import os
    import urllib.request

    host = os.environ.get("OLLAMA_HOST") or "127.0.0.1:11434"
    host = (host if host.startswith("http") else f"http://{host}").rstrip("/")
    req = urllib.request.Request(host + "/api/embed", headers={"Content-Type": "application/json"},
                                 data=json.dumps({"model": model, "input": texts, "truncate": True}).encode())
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=600) as r:
        v = np.asarray(json.loads(r.read())["embeddings"], dtype="float32")
    return v / np.clip(np.linalg.norm(v, axis=1, keepdims=True), 1e-9, None)


# ------------------------------------------------------------------ text base
class TextBase:
    def __init__(self, out_dir: str | Path = "data/out", config: str | Path | None = None):
        self.out = Path(out_dir)
        self.con = sqlite3.connect(self.out / "rctb.sqlite", check_same_thread=False)
        self.cfg = Config(config) if config else None
        self._index = None
        self.last_warning = None

    def table(self, name: str) -> pd.DataFrame:
        return pd.read_sql(f"SELECT * FROM {name}", self.con)

    documents = property(lambda self: self.table("documents"))
    pages = property(lambda self: self.table("pages"))
    entities = property(lambda self: self.table("entities"))
    files = property(lambda self: self.table("files"))

    def sql(self, query: str, params=()) -> pd.DataFrame:
        return pd.read_sql(query, self.con, params=params)

    @staticmethod
    def _filters(filters: dict) -> tuple[str, list]:
        """club=..., genre=..., file_number=..., year_from=..., year_to=..."""
        sql, params = "", []
        for k, v in filters.items():
            if v in (None, "", []):
                continue
            if k == "year_from":
                sql += " AND d.year >= ?"; params.append(v)
            elif k == "year_to":
                sql += " AND d.year <= ?"; params.append(v)
            elif k in ("club", "genre", "file_number", "country", "doc_id"):
                vals = v if isinstance(v, (list, tuple)) else [v]
                sql += f" AND d.{k} IN ({','.join('?' * len(vals))})"; params += list(vals)
            else:
                raise ValueError(f"unknown filter {k}")
        return sql, params

    COLS = "d.doc_id, d.club, d.genre, d.title, d.date_produced, d.year, d.start_page, d.end_page, d.file_number"

    def keyword(self, query: str, k: int = 10, **filters) -> pd.DataFrame:
        """Full-text search. Latin/Cyrillic queries use the FTS5 index (BM25 ranking; supports
        "exact phrases", AND/OR/NOT, prefix*). Queries containing Chinese/Japanese characters use
        substring matching, because those scripts are not split into words."""
        fsql, fparams = self._filters(filters)
        if CJK.search(query):
            df = self.sql(f"SELECT {self.COLS}, 1.0 AS score, substr(d.full_text, max(1, instr(d.full_text, ?) - 60), 160) AS snippet "
                          f"FROM documents d WHERE d.full_text LIKE ? {fsql} LIMIT ?", [query, f"%{query}%", *fparams, k])
        else:
            q = query if re.search(r'["*]|\b(AND|OR|NOT)\b', query) else " ".join(f'"{t}"' for t in re.findall(r"\w+", query))
            df = self.sql(f"SELECT {self.COLS}, -bm25(documents_fts) AS score, "
                          f"snippet(documents_fts, 3, '[', ']', ' ... ', 24) AS snippet "
                          f"FROM documents_fts JOIN documents d USING (doc_id) WHERE documents_fts MATCH ? {fsql} "
                          f"ORDER BY bm25(documents_fts) LIMIT ?", [q, *fparams, k])
        return df

    # ---- passage retrieval
    def _load_index(self):
        if self._index is None:
            idx = self.out / "index"
            meta = json.loads((idx / "meta.json").read_text())
            chunks = pd.read_parquet(idx / "chunks.parquet")
            if meta["backend"] == "sentence_transformers":
                from sentence_transformers import SentenceTransformer

                model, emb = SentenceTransformer(meta["model"]), np.load(idx / "embeddings.npy")
                score = lambda q: emb @ model.encode([q], normalize_embeddings=True)[0]
            elif meta["backend"] == "ollama":
                emb = np.load(idx / "embeddings.npy")
                score = lambda q: emb @ ollama_embed(meta["model"], [q])[0]
            else:
                try:
                    import warnings
                    with warnings.catch_warnings():
                        warnings.simplefilter("error")       # an index saved by another scikit-learn version: do not trust it
                        with open(idx / "tfidf.pkl", "rb") as fh:
                            t = pickle.load(fh)
                except Exception:                              # rebuild it in memory from the passages (a second or two)
                    from sklearn.feature_extraction.text import TfidfVectorizer

                    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True, min_df=1, lowercase=True)
                    t = {"vectorizer": vec, "matrix": vec.fit_transform(chunks["text"])}
                score = lambda q: np.asarray((t["matrix"] @ t["vectorizer"].transform([q]).T).todense()).ravel()
            self._index = (meta, chunks, score)
        return self._index

    def semantic(self, query: str, k: int = 10, **filters) -> pd.DataFrame:
        """Nearest passages by embedding similarity (cross-lingual with a multilingual model)."""
        meta, chunks, score = self._load_index()
        res = chunks.assign(score=score(query))
        docs = self.sql(f"SELECT {self.COLS} FROM documents d WHERE 1=1 {self._filters(filters)[0]}", self._filters(filters)[1])
        res = res.merge(docs, on="doc_id").sort_values("score", ascending=False).head(k)
        return res.reset_index(drop=True)

    STOP = set("about after also been between club clubs could does from have into more most much over rotary "
               "than that their them then there these they this those what when where which while with would "
               "were will your full less did why who how was the and for not any are had has his her its".split())

    def _any_word_query(self, query: str) -> str:
        """A question as a keyword query: match documents containing ANY of its content words
        (BM25 then ranks those sharing more, and rarer, words first)."""
        if CJK.search(query) or re.search(r'["*]|\b(AND|OR|NOT)\b', query):
            return query
        words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2 and w not in self.STOP]
        return " OR ".join(f'"{w}"' for w in dict.fromkeys(words)) or query

    def hybrid(self, query: str, k: int = 10, **filters) -> pd.DataFrame:
        """Reciprocal-rank fusion: every passage is ranked by embedding similarity, every document by
        keyword match (BM25); a passage scores well if it is near the top of either ranking."""
        kw = self.keyword(self._any_word_query(query), k=50, **filters)
        try:
            sem = self.semantic(query, k=10**9, **filters)                  # all passages, best first
        except OSError as e:  # the embedding server is not reachable (e.g. Ollama not running): keyword ranking only
            self.last_warning = f"semantic search unavailable ({e}); keyword results only"
            _, chunks, _ = (json.loads((self.out / "index" / "meta.json").read_text()),
                            pd.read_parquet(self.out / "index" / "chunks.parquet"), None)
            docs = self.sql(f"SELECT {self.COLS} FROM documents d WHERE 1=1 {self._filters(filters)[0]}", self._filters(filters)[1])
            order = {d: i for i, d in enumerate(kw["doc_id"])}
            sem = chunks.merge(docs, on="doc_id")
            sem = sem[sem["doc_id"].isin(order)].assign(score=0.0)
            sem = sem.sort_values("doc_id", key=lambda c: c.map(order), kind="stable").reset_index(drop=True)
        kw_rank = {d: i for i, d in enumerate(kw["doc_id"])}
        first = ~sem["doc_id"].duplicated()   # the keyword bonus goes to a document's best passage only
        sem = sem.assign(rrf=[1 / (60 + i) + (1 / (60 + kw_rank[d]) if (d in kw_rank and f) else 0)
                              for i, (d, f) in enumerate(zip(sem["doc_id"], first))])
        return sem.sort_values("rrf", ascending=False).head(k).reset_index(drop=True)

    # ---- RAG
    def ask(self, question: str, k: int = 5, language: str = "en", **filters) -> dict:
        """Retrieve the top-k passages, have the model answer from them only, and verify that every
        quoted citation literally occurs in the cited passage. Needs `config` and an LLM backend."""
        from .llm import LLM, load_prompt
        from .models import RagAnswer, json_schema

        if self.cfg is None:
            raise ValueError("TextBase(..., config='config.yaml') is needed for ask()")
        hits = self.hybrid(question, k=k, **filters)
        sources = []
        for i, h in enumerate(hits.to_dict("records"), 1):
            sources.append({"label": f"S{i}", **{c: h[c] for c in ("doc_id", "chunk_id", "club", "genre", "title", "date_produced", "pages", "text")}})
        block = "\n\n".join(f'<source id="{s["label"]}" doc_id="{s["doc_id"]}" club="{s["club"]}" genre="{s["genre"]}" '
                            f'date="{s["date_produced"]}" pages="{s["pages"]}">\n{s["text"]}\n</source>' for s in sources)
        lang = {"en": "English", "zh": "Chinese (中文)"}.get(language, language)
        llm = LLM(self.cfg, "rag")
        res, meta = llm.call("rag", load_prompt(self.cfg, "rag_answer"),
                             f"Answer language: {lang}\n\nQuestion: {question}\n\nSources:\n{block}", [], json_schema(RagAnswer), validate=RagAnswer)
        by_label = {s["label"]: s for s in sources}
        for c in res.get("citations", []):
            src = by_label.get(c["source"])
            c["doc_id"] = src["doc_id"] if src else None
            c["pages"] = src["pages"] if src else None
            c["verified"] = bool(src) and norm_ws(c["quote"]).lower() in norm_ws(src["text"]).lower()
        return {"question": question, "answer": res["answer"], "citations": res.get("citations", []),
                "sources": sources, "model": meta.get("model"), "k": k, "language": language}
