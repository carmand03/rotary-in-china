"""Compare the pipeline's output for a folder with a reference annotation (gold/*.yaml).

Writes to data/out/eval/:
  <file>_segmentation.html   every page as a thumbnail with the reference and the predicted document
                             boundaries side by side; disagreements highlighted, with the model's reason
  <file>_pages.csv           the same, one row per page
  <file>_fields.csv          for documents whose pages match exactly: reference vs predicted metadata

The reference is an annotation like any other: where the report shows the model was right, correct the
gold file, then run `evaluate` again.
"""
from __future__ import annotations

import csv
import html
from pathlib import Path

import yaml

from .s02_llm_stages import final_labels, labels_to_docs, load_pages
from .util import Config, doc_id_for, norm_ws, read_jsonl

FIELDS = ["genre", "title", "date_produced", "date_received", "place_origin", "place_destination",
          "creators", "recipients", "languages"]


def _flat(e: dict) -> dict:
    """The comparable core of a record (reference or predicted)."""
    def date(kind):
        return next((d.get("value") for d in e.get("dates") or [] if d.get("type") == kind and d.get("value")), None)

    def q(k):
        v = e.get(k) or {}
        return v.get("value") if isinstance(v, dict) else v

    return {"genre": e.get("genre"), "title": q("title"), "date_produced": date("produced"), "date_received": date("received"),
            "place_origin": q("place_origin"), "place_destination": q("place_destination"),
            "creators": "; ".join(sorted(a["name"] for a in e.get("creators") or [])),
            "recipients": "; ".join(sorted(a["name"] for a in e.get("recipients") or [])),
            "languages": ",".join(sorted(e.get("languages") or []))}


def _same(field: str, a, b) -> bool:
    if field == "title":   # titles of untitled items are free descriptions: not comparable word for word
        return bool(a) == bool(b)
    return norm_ws(str(a or "")).casefold() == norm_ws(str(b or "")).casefold()


def run(cfg: Config, gold_path: str | Path) -> dict:
    g = yaml.safe_load(Path(gold_path).read_text(encoding="utf-8"))
    fn = g["file_number"]
    pages = load_pages(cfg, fn)
    labels, seg_meta = final_labels(cfg, fn)
    if not labels:
        raise SystemExit(f"evaluate: {fn} has no segmentation yet (run `segment`).")
    pred_docs = labels_to_docs(labels)[0]
    gold_docs = [d["pages"] for d in g["documents"]]
    by_label = {l["page"]: l for l in labels}

    def owner(docs):
        return {p: d[0] for d in docs for p in d}

    gold_of, pred_of = owner(gold_docs), owner(pred_docs)
    gold_starts, pred_starts = {d[0] for d in gold_docs}, {d[0] for d in pred_docs}
    gold_blank = set(g.get("blank_pages", []))
    rows = []
    for r in pages:
        p = r["page"]
        gs, ps = p in gold_starts, p in pred_starts
        g_blank, p_blank = p in gold_blank or p not in gold_of, p not in pred_of
        if g_blank != p_blank:
            status = "reference: blank, predicted: content" if g_blank else "reference: content, predicted: blank"
        elif gs and not ps:
            status = "missed start (predicted: continues the previous document)"
        elif ps and not gs:
            status = "extra start (reference: continues the previous document)"
        else:
            status = "agree"
        lab = by_label.get(p, {})
        rows.append({"page": p, "status": status,
                     "reference_document": "blank" if g_blank else doc_id_for(fn, gold_of[p]),
                     "predicted_document": "blank" if p_blank else doc_id_for(fn, pred_of[p]),
                     "predicted_relation": lab.get("relation"), "model_reason": lab.get("reason") or lab.get("note") or "",
                     "text_start": norm_ws(r["text"])[:140], "image": r["image"]})

    tp = len(gold_starts & pred_starts)
    exact = [d for d in gold_docs if d in pred_docs]
    res = {"file_number": fn, "method": seg_meta.get("method"), "model": seg_meta.get("model"),
           "reference_documents": len(gold_docs), "predicted_documents": len(pred_docs),
           "boundary_precision": round(tp / len(pred_starts), 3) if pred_starts else None,
           "boundary_recall": round(tp / len(gold_starts), 3) if gold_starts else None,
           "documents_matching_exactly": len(exact),
           "missed_starts": sorted(gold_starts - pred_starts), "extra_starts": sorted(pred_starts - gold_starts),
           "pages_agreeing": sum(r["status"] == "agree" for r in rows), "pages": len(rows)}

    out = cfg.out / "eval"
    out.mkdir(parents=True, exist_ok=True)
    with open(out / f"{fn}_pages.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[k for k in rows[0] if k != "image"], extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    # ---- metadata, for documents on which both sides agree about the pages
    enriched = {r["doc_id"]: r["enrichment"] for r in read_jsonl(cfg.work / "enriched" / f"{fn}.jsonl")}
    gold_by_id = {doc_id_for(fn, d["pages"][0]): d for d in g["documents"]}
    frows, agree = [], {k: [0, 0] for k in FIELDS}
    for d in exact:
        doc_id = doc_id_for(fn, d[0])
        if doc_id not in enriched:
            continue
        a, b = _flat(gold_by_id[doc_id]), _flat(enriched[doc_id])
        for k in FIELDS:
            same = _same(k, a[k], b[k])
            agree[k][0] += same
            agree[k][1] += 1
            frows.append({"doc_id": doc_id, "field": k, "same": same, "reference": a[k], "predicted": b[k]})
    with open(out / f"{fn}_fields.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["doc_id", "field", "same", "reference", "predicted"])
        w.writeheader()
        w.writerows(frows)
    res["field_agreement"] = {k: f"{a}/{n}" for k, (a, n) in agree.items() if n}

    # ---- visual report
    colors = {"agree": "#ffffff"}
    trs, prev_g, prev_p = [], None, None
    for r in rows:
        bg = colors.get(r["status"], "#fff3cd")
        g_new = r["reference_document"] != prev_g and r["reference_document"] != "blank"
        p_new = r["predicted_document"] != prev_p and r["predicted_document"] != "blank"
        prev_g, prev_p = r["reference_document"], r["predicted_document"]

        def cell(doc, new):
            if doc == "blank":
                return "<td class='blank'>blank</td>"
            return f"<td class='{'start' if new else 'cont'}'>{'▶ ' + html.escape(doc) if new else '│'}</td>"

        trs.append(
            f"<tr style='background:{bg}'><td class='n'>{r['page']}</td>"
            f"<td><a href='../{html.escape(r['image'])}' target='_blank'><img loading='lazy' src='../{html.escape(r['image'])}'></a></td>"
            f"{cell(r['reference_document'], g_new)}{cell(r['predicted_document'], p_new)}"
            f"<td>{'' if r['status'] == 'agree' else '<b>' + html.escape(r['status']) + '</b><br>'}"
            f"<span class='reason'>{html.escape(r['model_reason'])}</span><br><span class='txt'>{html.escape(r['text_start'])}</span></td></tr>")
    summary = (f"{res['pages_agreeing']} of {res['pages']} pages agree · reference {res['reference_documents']} documents, "
               f"predicted {res['predicted_documents']}, {res['documents_matching_exactly']} identical · "
               f"boundary precision {res['boundary_precision']}, recall {res['boundary_recall']} · "
               f"method {res['method']}, model {res['model']}")
    (out / f"{fn}_segmentation.html").write_text(f"""<!doctype html><meta charset="utf-8"><title>{fn} segmentation</title>
<style>body{{font:14px system-ui,sans-serif;margin:24px;color:#222}}table{{border-collapse:collapse}}
td,th{{border:1px solid #ccc;padding:6px 10px;vertical-align:top;text-align:left}}img{{height:190px;display:block}}
.n{{font-weight:600}}.start{{font-weight:600;white-space:nowrap}}.cont{{color:#888;text-align:center}}.blank{{color:#999;font-style:italic}}
.reason{{color:#333}}.txt{{color:#777;font-size:12px}}th{{background:#f3f3f3;position:sticky;top:0}}</style>
<h2>{fn}: document boundaries, reference vs predicted</h2><p>{html.escape(summary)}</p>
<p>▶ = a document starts on this page; │ = the page continues the document above. Highlighted rows are disagreements.
Click a thumbnail to open the page. Reference: {html.escape(str(gold_path))}.</p>
<table><tr><th>Page</th><th>Image</th><th>Reference</th><th>Predicted</th><th>Disagreement · model's reason · start of text</th></tr>
{''.join(trs)}</table>""", encoding="utf-8")
    print(res)
    print(f"evaluate: report written to {out}/{fn}_segmentation.html (+ _pages.csv, _fields.csv)")
    return res
