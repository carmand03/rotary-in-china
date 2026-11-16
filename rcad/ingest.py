"""Stage 1 - ingest: your per-club workbooks and notes -> attestations.

An attestation is one statement found in one source, kept exactly as you recorded it:
"the roster on B37-F01 p.4 lists Ahnert, E. E., Mining Engineer, Mulin Coal Mining Co., Adviser".
Nothing is merged or corrected here. Each row remembers the file, sheet and row it came from.

Inputs (actors/sources/):  <Club>.xlsx  with sheets members, officers, mbr_years, mbr_month, attend_month
                           <Club>.docx  notes, organised by folder ("B36-F05") and page ("p.11-12 : ...")
Outputs (actors/work/):    att_membership.csv, att_office.csv, att_club_stat.csv, note.csv, club_header.csv
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

import openpyxl
import pandas as pd

REF = re.compile(r"(B\d{1,2}-F\d{1,2})-(\d{1,4})")
EMPTY = {"", "—", "-", "–", "none", "nan"}


def clean(v):
    """Cell value as trimmed text; dashes used for 'nothing' become empty."""
    if v is None:
        return None
    if isinstance(v, float) and v == int(v):
        v = int(v)
    s = re.sub(r"\s+", " ", str(v)).strip()
    return None if s.lower() in EMPTY else s


def ym(year, month=None) -> str | None:
    """1936, 6 -> '1936-06'; 1936 -> '1936'."""
    y = clean(year)
    if not y or not re.fullmatch(r"\d{4}", y):
        return None
    m = clean(month)
    return f"{y}-{int(m):02d}" if m and m.isdigit() and 1 <= int(m) <= 12 else y


def iso(v) -> str | None:
    if isinstance(v, (dt.datetime, dt.date)):
        return v.strftime("%Y-%m-%d")
    return None


def sheet_rows(wb, name: str):
    """Rows of a sheet as dicts keyed by header (blank header columns ignored), with their Excel row number."""
    if name not in wb.sheetnames:
        return
    rows = list(wb[name].iter_rows(values_only=True))
    header = [clean(h) for h in rows[0]]
    for i, r in enumerate(rows[1:], start=2):
        d = {h: v for h, v in zip(header, r) if h}
        if any(clean(v) for v in d.values()):
            yield i, d


def col(d: dict, *starts):
    """Value of the first column whose header starts with one of the given words (headers vary a little between files)."""
    for s in starts:
        for k, v in d.items():
            if k.lower().startswith(s.lower()):
                return v
    return None


def ingest_workbook(path: Path, club: str) -> dict[str, list[dict]]:
    wb = openpyxl.load_workbook(path, data_only=True)
    out = {"att_membership": [], "att_office": [], "att_club_stat": []}
    prov = lambda sheet, i: {"club": club, "input_file": path.name, "input_sheet": sheet, "input_row": i}

    for i, d in sheet_rows(wb, "members"):
        el = col(d, "Election")
        out["att_membership"].append({
            **prov("members", i),
            "surname": clean(col(d, "Surname")), "given": clean(col(d, "Given")), "nickname": clean(col(d, "Nickname")),
            "vernacular_name": clean(col(d, "Vernacular Name", "Vernacular")),
            "classification": clean(col(d, "Classification (EN)")), "classification_vernacular": clean(col(d, "Classification (ZH")),
            "classification_no": clean(col(d, "Classification No")),
            "institution": clean(col(d, "Company Name")), "institution_vernacular": clean(col(d, "Company (Vernacular)")),
            "position": clean(col(d, "Position")), "business_address": clean(col(d, "Business Address")),
            "private_address": clean(col(d, "Private Address (EN)")), "private_address_vernacular": clean(col(d, "Private Address (Vernacular)")),
            "subscribes_rotarian": clean(col(d, "Subscription")), "former_rotarian": clean(col(d, "Former Rotarian")),
            "election_date": iso(el), "charter_member": isinstance(el, str) and "charter" in el.lower(),
            "election_as_written": None if isinstance(el, (dt.datetime, dt.date)) else clean(el),
            "date_start": ym(col(d, "Year_Start"), col(d, "Month_Start")),
            "date_end": ym(col(d, "Year_End"), col(d, "Year_Month")),
            "source_refs": clean(col(d, "Source"))})

    for i, d in sheet_rows(wb, "officers"):
        out["att_office"].append({
            **prov("officers", i),
            "surname": clean(col(d, "Surname")), "given": clean(col(d, "Given")), "vernacular_name": clean(col(d, "Vernacular")),
            "position": clean(col(d, "Position")), "body": clean(col(d, "Committee")),
            "election_date": iso(col(d, "Election")), "date_start": ym(col(d, "Year_start"), col(d, "Month")),
            "date_end": ym(col(d, "Year_end")), "address": clean(col(d, "Address")), "classification": clean(col(d, "Classification")),
            "source_refs": clean(col(d, "Source"))})

    # the three statistics sheets become one long table: club, date, measure, value
    measures = {"mbr_years": {"Members": "members"},
                "mbr_month": {"Net": "members", "Accessions": "accessions", "Terminations": "terminations"},
                "attend_month": {"No. of Meetings": "meetings", "Average Membership": "average_membership",
                                 "Average Attendance": "average_attendance", "Average Percentage": "attendance_percent"}}
    for sheet, cols in measures.items():
        for i, d in sheet_rows(wb, sheet):
            for header, measure in cols.items():
                v = clean(d.get(header))
                try:
                    value = float(v) if v is not None else None
                except ValueError:
                    value = None
                if value is not None:
                    out["att_club_stat"].append({**prov(sheet, i), "date": ym(d.get("Year"), d.get("Month")), "measure": measure,
                                                 "value": value, "source_refs": clean(d.get("Source"))})
    return out


PAGE_LINE = re.compile(r"^(?:p\.{0,2}\s*)?(\d{1,4})\s*(?:[-–]\s*(\d{1,4}))?((?:\s*,\s*\d{1,4}(?:\s*[-–]\s*\d{1,4})?)*)\s*[:：]\s*(.*)$", re.S | re.I)
PAGE_LOOSE = re.compile(r"^p\.{1,2}\s*(\d{1,4})\s*(?:[-–]\s*(\d{1,4}))?\s+(.*)$", re.S | re.I)


def docx_paragraphs(path: Path) -> list[str]:
    """Paragraph texts of a Word file, read directly from the .docx (no extra software needed)."""
    import zipfile
    import xml.etree.ElementTree as ET

    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    root = ET.fromstring(zipfile.ZipFile(path).read("word/document.xml"))
    out = []
    for p in root.iter(W + "p"):
        parts = []
        for node in p.iter():
            if node.tag == W + "t":
                parts.append(node.text or "")
            elif node.tag in (W + "tab", W + "br"):
                parts.append(" ")
        out.append(re.sub(r"\s+", " ", "".join(parts)).strip())
    return out


def ingest_notes(path: Path, club: str) -> tuple[list[dict], list[dict]]:
    """Notes are paragraphs. A line that is just a folder number (B36-F05) opens that folder; a paragraph
    starting with page numbers ("p.11-12 : ...") opens a note; other paragraphs continue the note above."""
    paras = [p for p in docx_paragraphs(path) if p]
    notes, header, folder = [], [], None
    for p in paras:
        if re.fullmatch(r"B\d{1,2}-F\d{1,2}", p):
            folder = p
            continue
        if folder is None:
            header.append({"club": club, "input_file": path.name, "line": p})
            continue
        m = PAGE_LINE.match(p) or PAGE_LOOSE.match(p)
        if m:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else start
            body = m.groups()[-1].strip()
            notes.append({"club": club, "input_file": path.name, "file_number": folder, "pages_as_written": p[:m.start(len(m.groups()))].strip(" :："),
                          "page_start": start, "page_end": max(start, end) if end - start < 400 else start, "text": body})
        elif notes and notes[-1]["file_number"] == folder:
            notes[-1]["text"] += "\n" + p.lstrip("- ").strip()
        else:
            notes.append({"club": club, "input_file": path.name, "file_number": folder, "pages_as_written": None,
                          "page_start": None, "page_end": None, "text": p})
    for i, n in enumerate(notes, 1):
        n["note_id"] = f"N-{club}-{i:04d}"
    return notes, header


EXPECTED_SHEETS = ["members", "officers", "mbr_years", "mbr_month", "attend_month"]
KNOWN_HEADERS = {
    "members": ["surname", "given", "nickname", "vernacular", "classification", "company", "position", "business address",
                "private address", "subscription", "former rotarian", "election", "year_start", "month_start", "year_end",
                "year_month", "source"],
    "officers": ["surname", "given", "vernacular", "election", "position", "committee", "year_start", "month", "year_end",
                 "source", "address", "classification"],
}


def check_workbook(path: Path) -> list[str]:
    """What in a workbook the reader will not understand. Reported, never corrected."""
    wb = openpyxl.load_workbook(path, data_only=True)
    out = []
    missing = [x for x in EXPECTED_SHEETS if x not in wb.sheetnames]
    extra = [x for x in wb.sheetnames if x not in EXPECTED_SHEETS]
    if missing:
        out.append(f"sheets not found (their data is simply absent): {', '.join(missing)}")
    if extra:
        out.append(f"sheets ignored (name not recognised): {', '.join(extra)}")
    for sheet in wb.sheetnames:
        if sheet not in EXPECTED_SHEETS:
            continue
        rows = list(sheet_rows(wb, sheet))
        headers = list(rows[0][1]) if rows else []
        if sheet in KNOWN_HEADERS:
            unknown = [h for h in headers if not any(h.lower().startswith(k) for k in KNOWN_HEADERS[sheet])]
            if unknown:
                out.append(f"`{sheet}`: columns ignored (header not recognised): {', '.join(unknown)}")
            need = [k for k in ("surname", "source") if not any(h.lower().startswith(k) for h in headers)]
            if need:
                out.append(f"`{sheet}`: required column missing: {', '.join(need)}")
            no_name = [i for i, d in rows if not clean(col(d, "Surname"))]
            if no_name:
                out.append(f"`{sheet}`: {len(no_name)} row(s) without a surname are skipped: rows {no_name[:10]}")
        src = lambda d: clean(col(d, "Source"))
        no_src = [i for i, d in rows if not src(d)]
        bad_src = [(i, src(d)) for i, d in rows if src(d) and not REF.search(src(d))]
        if no_src:
            out.append(f"`{sheet}`: {len(no_src)} row(s) without a source: rows {no_src[:10]}{' ...' if len(no_src) > 10 else ''}")
        if bad_src:
            out.append(f"`{sheet}`: {len(bad_src)} source reference(s) not in the form B37-F01-04, so not linked: "
                       + "; ".join(f"row {i}: {v}" for i, v in bad_src[:6]))
        years = [(i, v) for i, d in rows for k, v in d.items()
                 if k.lower().startswith("year") and not k.lower().startswith("year_month") and clean(v) and not re.fullmatch(r"\d{4}", clean(v))]
        if years:
            out.append(f"`{sheet}`: {len(years)} year value(s) that are not a 4-digit year: " + "; ".join(f"row {i}: {v}" for i, v in years[:6]))
    return out


def check_notes(notes: list[dict], header: list[dict]) -> list[str]:
    out = []
    folders = sorted({n["file_number"] for n in notes})
    out.append(f"folders found: {', '.join(folders) or 'none'}; {len(notes)} notes; {len(header)} header line(s) before the first folder")
    if not folders:
        out.append("no line consisting only of a folder number (e.g. B36-F05) was found, so no note could be attached to pages")
    loose = [n for n in notes if n["page_start"] is None]
    if loose:
        out.append(f"{len(loose)} paragraph(s) directly under a folder line without page numbers, kept as notes on the folder: "
                   + "; ".join(repr(n["text"][:40]) for n in loose[:5]))
    wide = [n for n in notes if n["page_start"] and n["page_end"] - n["page_start"] > 60]
    if wide:
        out.append(f"{len(wide)} note(s) cover more than 60 pages (check the page range): " + "; ".join(n["pages_as_written"] for n in wide[:5]))
    back = [b["pages_as_written"] for a, b in zip(notes, notes[1:])
            if a["file_number"] == b["file_number"] and a["page_start"] and b["page_start"] and b["page_start"] < a["page_start"] - 30]
    if back:
        out.append(f"{len(back)} note(s) whose page number is much lower than the note before (typing slip, or pages cited out of order?): "
                   + "; ".join(back[:6]))
    return out


def run(base: Path) -> None:
    src, work = base / "sources", base / "work"
    work.mkdir(parents=True, exist_ok=True)
    report = ["# Input report", "", "What the reader could not use in your files. Nothing here was changed or guessed;",
              "fix the file if the remark matters, and run `python -m rcad all` again.", ""]
    tables: dict[str, list[dict]] = {"att_membership": [], "att_office": [], "att_club_stat": [], "note": [], "club_header": []}
    for path in sorted(src.glob("*.xlsx")):
        if path.name.startswith("~$"):
            continue
        for k, rows in ingest_workbook(path, path.stem).items():
            tables[k] += rows
        remarks = check_workbook(path)
        report += [f"## {path.name}", ""] + [f"- {r}" for r in remarks or ["nothing to report"]] + [""]
    for path in sorted(src.glob("*.docx")):
        if path.name.startswith("~$"):
            continue
        notes, header = ingest_notes(path, path.stem)
        tables["note"] += notes
        tables["club_header"] += header
        report += [f"## {path.name}", ""] + [f"- {r}" for r in check_notes(notes, header)] + [""]
    for name, rows in tables.items():
        df = pd.DataFrame(rows)
        if name.startswith("att_") and len(df):
            df.insert(0, "att_id", [f"{name[4:7].upper()}-{r.club}-{r.input_sheet}-{r.input_row}" for r in df.itertuples()])
        df.to_csv(work / f"{name}.csv", index=False, encoding="utf-8-sig")
    (work / "input_report.md").write_text("\n".join(report), encoding="utf-8")
    n_remarks = sum(1 for line in report if line.startswith("- ") and "nothing to report" not in line and not line.startswith("- folders found"))
    print(f"ingest: {n_remarks} remark(s) on the input files -> {work / 'input_report.md'}")
    print("ingest: " + ", ".join(f"{len(v)} {k}" for k, v in tables.items()) + f" -> {work}")
