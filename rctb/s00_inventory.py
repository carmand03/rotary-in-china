"""Stage 0 - inventory: index spreadsheet + PDFs on disk -> data/work/files.jsonl.

One record per PDF/folder with normalised club name, volume, ISO dates, checksum and page count.
Nothing here is interpreted by a model; mismatches with the index are flagged, not fixed.
"""
from __future__ import annotations

import datetime as dt
import re

import openpyxl
from pypdf import PdfReader

from .util import Config, log_run, sha256_file, write_jsonl

MONTHS = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}


def iso_date(v) -> str | None:
    """'1933-Oct-23', datetime, 1931, '1948-03' -> ISO string (possibly truncated)."""
    if v is None or v == "":
        return None
    if isinstance(v, (dt.datetime, dt.date)):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    m = re.fullmatch(r"(\d{4})-([A-Za-z]{3})-(\d{1,2})", s)
    if m and m.group(2).title() in MONTHS:
        return f"{m.group(1)}-{MONTHS[m.group(2).title()]:02d}-{int(m.group(3)):02d}"
    if re.fullmatch(r"\d{4}(-\d{2}(-\d{2})?)?", s):
        return s
    return s  # left as-is and visible in files.jsonl


def existing_folder(cfg: Config, f: dict):
    """Your folder of page images and text files for this PDF, if configured and present."""
    ex = cfg["existing_pages"]
    if not ex.get("enabled"):
        return None
    cands = [cfg.base / ex["dir"] / t.format(stem=f["pdf_stem"], file_number=f["file_number"]) for t in ex["folder_patterns"]]
    return next((c for c in cands if c.is_dir()), None)


def count_folder_pages(cfg: Config, folder) -> int:
    """Pages are numbered from 1 without gaps: count until neither an image nor a text file exists."""
    ex, n = cfg["existing_pages"], 0
    while any((folder / t.format(page=n + 1)).exists() for t in ex["png_patterns"] + ex["txt_patterns"]):
        n += 1
    return n


def run(cfg: Config) -> list[dict]:
    ws = openpyxl.load_workbook(cfg.p("index_xlsx"), data_only=True).active
    rows = list(ws.iter_rows(values_only=True))
    header = [str(h).strip() for h in rows[0]]
    coll = cfg["project"]["collection_id"]
    pdf_dir = cfg.p("pdf_dir")
    files = []
    for r in rows[1:]:
        row = dict(zip(header, r))
        if row.get("BOX") is None:
            continue
        box, folder = int(row["BOX"]), int(row["FOLDER"])
        title = re.sub(r"\s+", " ", str(row["CLUB/FOLDER TITLE"]).replace("\xa0", " ")).strip()
        vol = re.search(r"vol\.\s*(\d+)", title)
        file_number = f"B{box:02d}-F{folder:02d}"
        stem = f"{coll}-{file_number}"
        pdf = pdf_dir / f"{stem}.pdf"
        rec = {
            "file_number": file_number, "pdf_stem": stem, "box": box, "folder": folder,
            "country": row.get("COUNTRY"), "folder_title": title,
            "club": re.sub(r"\s*vol\.\s*\d+", "", title).strip(),
            "volume": int(vol.group(1)) if vol else None,
            "organized": iso_date(row.get("ORGANIZED")), "chartered": iso_date(row.get("CHARTERED")),
            "terminated": iso_date(row.get("TERMINATED")),
            "index_pages": row.get("Pages"), "index_start": iso_date(row.get("Start")),
            "index_end": iso_date(row.get("End")), "index_status": row.get("Status"),
            "pdf_present": pdf.exists(), "pdf_path": str(pdf.relative_to(cfg.base)) if pdf.exists() else None,
            "pdf_sha256": None, "n_pages": None, "flags": [],
        }
        folder = existing_folder(cfg, rec)
        n_folder = count_folder_pages(cfg, folder) if folder else 0
        rec["pages_folder"] = str(folder.relative_to(cfg.base)) if folder else None
        rec["available"] = pdf.exists() or n_folder > 0     # something to work from
        if not pdf.exists() and n_folder:
            rec["n_pages"] = n_folder                         # no PDF here: page count from the image/text files
        if pdf.exists() and n_folder and n_folder != len(PdfReader(str(pdf)).pages):
            rec["flags"].append(f"page folder has {n_folder} pages, PDF has {len(PdfReader(str(pdf)).pages)}")
        if rec["index_pages"] and rec["n_pages"] and not pdf.exists() and int(rec["index_pages"]) != rec["n_pages"]:
            rec["flags"].append(f"index says {rec['index_pages']} pages, page folder has {rec['n_pages']}")
        if pdf.exists():
            rec["pdf_sha256"] = sha256_file(pdf)
            rec["n_pages"] = len(PdfReader(str(pdf)).pages)
            if rec["index_pages"] and int(rec["index_pages"]) != rec["n_pages"]:
                rec["flags"].append(f"index says {rec['index_pages']} pages, PDF has {rec['n_pages']}")
        files.append(rec)
    known = {f["pdf_stem"] for f in files}
    orphans = sorted(p.name for p in pdf_dir.glob("*.pdf") if p.stem not in known)
    write_jsonl(cfg.work / "files.jsonl", files)
    log_run(cfg, "inventory", n_index_rows=len(files), n_pdf=sum(f["pdf_present"] for f in files),
            pdfs_not_in_index=orphans)
    n_folders = sum(bool(f["pages_folder"]) for f in files)
    missing = [f["file_number"] for f in files if not f["available"]]
    if missing:
        print(f"inventory: nothing to work from for {len(missing)} index rows: {missing[:8]}{' ...' if len(missing) > 8 else ''}")
    print(f"inventory: {len(files)} index rows, {sum(f['pdf_present'] for f in files)} PDFs found, {n_folders} page folders found"
          + (f", {len(orphans)} PDFs not in index: {orphans}" if orphans else ""))
    return files
