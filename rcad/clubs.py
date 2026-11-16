"""Club identification: one statement per row, each with its source.

A club's name, dates, meeting day or language can change over time, so they are not columns of the club
table but rows of `actors/authority/club_statements.csv`:

    club_id, attribute, value, detail, date_from, date_to, source, status, note

attribute   name | charter_number | organized | chartered | admitted | terminated |
            meeting_day | meeting_time | meeting_place | language | publication
value       the name, number, date (YYYY-MM-DD, YYYY-MM or YYYY), day, time, place, language or title
            (for `publication`: the title, or `no` when you know the club had none)
detail      free complement (for `publication`: the periodicity)
date_from, date_to   when the value applied (a name used 1928-1949, a meeting day from 1935), if known
status      auto       found by rule in the archive index or the header of your notes;
                       recomputed at every build, so do not edit these rows
            confirmed  yours: added or checked by hand, kept for good (an empty status counts as confirmed)
            rejected   an `auto` row you refuse: it stays in the file and is left out of the database

To correct an `auto` row, set it to `rejected` and add your own row; or correct the notes header and rebuild.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from rctb.normalize import fold

from .resolve import read_csv_any, s

COLS = ["club_id", "attribute", "value", "detail", "date_from", "date_to", "source", "status", "note"]
EVENTS = ["organized", "chartered", "admitted", "terminated"]
ATTRIBUTES = ["name", "charter_number", *EVENTS, "meeting_day", "meeting_time", "meeting_place", "language", "publication"]
MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAY = re.compile(r"\b(" + "|".join(d[:3] for d in DAYS) + r")[a-z]*\b", re.I)
TIME = re.compile(r"\b(\d{1,2}(?:[:.h]\d{2})?\s*(?:[ap]\.?\s?m\.?)|\d{1,2}[:.h]\d{2}|noon|midday)", re.I)
NO = {"no", "none", "false", "0"}


def parse_date(text: str) -> str | None:
    """'24 April 1930', 'April 24, 1930', 'Apr. 1930', '1930-04-24', '1930' -> ISO date as precise as the text."""
    t = s(text).strip()
    m = re.search(r"\b(\d{4})-(\d{1,2})(?:-(\d{1,2}))?\b", t)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}" + (f"-{int(m.group(3)):02d}" if m.group(3) else "")
    y = re.search(r"\b(1[89]\d\d|20\d\d)\b", t)
    if not y:
        return None
    mon = next((MONTHS[w[:3].lower()] for w in re.findall(r"[A-Za-z]{3,}", t) if w[:3].lower() in MONTHS), None)
    if not mon:
        return y.group(1)
    rest = t[:y.start()] + t[y.end():]
    d = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\b", rest)
    return f"{y.group(1)}-{mon:02d}" + (f"-{int(d.group(1)):02d}" if d and 1 <= int(d.group(1)) <= 31 else "")


def parse_header_line(line: str) -> list[dict]:
    """Statements found in one header line of the notes. Unrecognised lines give nothing (they stay in club_fact)."""
    t = s(line).strip()
    low = t.lower()
    out: list[dict] = []
    m = re.search(r"\(\s*(?:charter\s*)?no\.?\s*(\d{2,5})\s*\)|^charter\s*(?:number|no\.?)\s*:?\s*(\d{2,5})", t, re.I)
    if m:
        out.append({"attribute": "charter_number", "value": m.group(1) or m.group(2)})
    for words, attr in ((("reorgani", "organi"), "organized"), (("charter",), "chartered"), (("readmit", "admit"), "admitted"), (("terminat",), "terminated")):
        if low.startswith(words) and not low.startswith("charter member") and not (attr == "chartered" and m):
            d = parse_date(t)
            if d:
                out.append({"attribute": attr, "value": d, "detail": re.split(r"[\s:]", t, maxsplit=1)[0].lower() if low.startswith("re") else ""})
    if low.startswith("meeting"):
        body = re.sub(r"^meetings?\s*:?\s*", "", t, flags=re.I)
        day, time = DAY.search(body), TIME.search(body)
        if day:
            out.append({"attribute": "meeting_day", "value": next(d for d in DAYS if d[:3].lower() == day.group(1).lower())})
        if time:
            out.append({"attribute": "meeting_time", "value": time.group(1)})
        place = body
        for x in (day, time):
            place = place.replace(x.group(0), "") if x else place
        place = re.sub(r"^[\s,;:.-]+|[\s,;:.-]+$", "", re.sub(r"\b(at|on|every)\b", " ", place, flags=re.I)).strip()
        if place:
            out.append({"attribute": "meeting_place", "value": re.sub(r"\s{2,}", " ", place)})
    m = re.match(r"languages?\s*:?\s*(.+)", t, re.I)
    if m:
        out += [{"attribute": "language", "value": v.strip()} for v in re.split(r",|;|/|\band\b", m.group(1)) if v.strip()]
    m = re.match(r"(?:publication|bulletin)s?\s*:\s*(.+)", t, re.I)
    if m:
        title, _, per = m.group(1).partition("(")
        out.append({"attribute": "publication", "value": title.strip(" ,;"), "detail": per.rstrip(") ").strip()})
    m = re.match(r"(?:also known as|other names?|alternative names?|renamed)\s*:?\s*(.+)", t, re.I)
    if m:
        for v in re.split(r";|,(?![^(]*\))", m.group(1)):
            name, _, per = v.partition("(")
            ys = re.findall(r"\b(1[89]\d\d)\b", per)
            if name.strip():
                out.append({"attribute": "name", "value": name.strip(), "date_from": ys[0] if ys else "", "date_to": ys[1] if len(ys) > 1 else ""})
    return out


def auto_statements(clubs: list[dict], files_of: dict[str, list[dict]], header: list[dict], club_id) -> list[dict]:
    """What the archive index and the notes headers say. `header`: rows of work/club_header.csv."""
    rows: list[dict] = []

    def add(cid, attribute, value, source, **kw):
        if value and not any(r["club_id"] == cid and r["attribute"] == attribute and fold(r["value"]) == fold(str(value)) for r in rows):
            rows.append({**dict.fromkeys(COLS, ""), "club_id": cid, "attribute": attribute, "value": str(value), "source": source, "status": "auto", **kw})

    for c in clubs:
        cid = c["club_id"]
        add(cid, "name", c["name"], "archive index")
        fs = files_of.get(cid, [])
        dated = sorted((f["chartered"], f["file_number"]) for f in fs if f.get("chartered"))
        if dated:   # the index gives one date per folder: the first is the charter, each distinct one an admission
            add(cid, "chartered", dated[0][0], f"archive index ({dated[0][1]})", note="first date of the index column CHARTERED")
        for f in fs:
            src = f"archive index ({f['file_number']})"
            add(cid, "organized", f.get("organized"), src)
            add(cid, "admitted", f.get("chartered"), src, note="index column CHARTERED")
            add(cid, "terminated", f.get("terminated"), src)
    for h in header:
        for st in parse_header_line(h["line"]):
            add(club_id(h["club"]), st.pop("attribute"), st.pop("value"), f"{h['input_file']} (header)", **st)
    return rows


def load_statements(path: Path, auto: list[dict]) -> pd.DataFrame:
    """Hand rows are kept as they are; `auto` rows are recomputed; a rejected row silences the same auto row."""
    old = read_csv_any(path)
    old = old.reindex(columns=COLS).fillna("").astype(str) if len(old) else pd.DataFrame(columns=COLS)
    old["status"] = old["status"].str.strip().str.lower().replace("", "confirmed")
    hand = old[old["status"] != "auto"]
    key = lambda r: (r["club_id"], r["attribute"], fold(r["value"]))
    taken = {key(r) for r in hand.to_dict("records")}
    df = pd.concat([hand, pd.DataFrame([r for r in auto if key(r) not in taken], columns=COLS)], ignore_index=True)
    df["_a"] = df["attribute"].map(lambda a: ATTRIBUTES.index(a) if a in ATTRIBUTES else 99)
    df = df.sort_values(["club_id", "_a", "value"], kind="stable").drop(columns="_a")
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return df[df["status"] != "rejected"]


def periods(st: pd.DataFrame) -> list[dict]:
    """Periods of existence of each club: every admission opens one, the next termination closes it."""
    out = []
    for cid, g in st[st["attribute"].isin(EVENTS)].groupby("club_id"):
        ev = sorted((r["value"], r["attribute"]) for r in g.to_dict("records") if r["value"])
        opens = [d for d, a in ev if a == "admitted"] or [d for d, a in ev if a in ("chartered", "organized")][:1]
        ends = [d for d, a in ev if a == "terminated"]
        for i, start in enumerate(opens):
            nxt = opens[i + 1] if i + 1 < len(opens) else "9999"
            end = next((e for e in ends if start <= e < nxt), None)
            out.append({"club_id": cid, "period": i + 1, "start": start, "end": end})
        if not opens:
            out += [{"club_id": cid, "period": i + 1, "start": None, "end": e} for i, e in enumerate(ends)]
    return out


def summarise(st: pd.DataFrame, cid: str) -> dict:
    """The columns of the `club` table that come from the statements; several values are joined with '; '."""
    g = st[st["club_id"] == cid]

    def vals(attr, with_period=False):
        rows = g[g["attribute"] == attr].sort_values(["date_from", "value"]).to_dict("records")
        seen = []
        for r in rows:
            per = "–".join(x for x in (r["date_from"], r["date_to"]) if x) if with_period else ""
            v = r["value"] + (f" ({per})" if per else "")
            if v not in seen:
                seen.append(v)
        return seen

    j = lambda xs: "; ".join(xs) or None
    pubs = g[g["attribute"] == "publication"].to_dict("records")
    real = [p for p in pubs if fold(p["value"]) not in NO]
    return {"charter_number": j(vals("charter_number")), "other_names": j(vals("name", True)[1:]) if len(vals("name")) > 1 else None,
            **{e: j(sorted(vals(e))) for e in EVENTS},
            "meeting_day": j(vals("meeting_day", True)), "meeting_time": j(vals("meeting_time", True)), "meeting_place": j(vals("meeting_place", True)),
            "languages": j(vals("language", True)),
            "publication": "yes" if real else ("no" if pubs else None),
            "publication_title": j([p["value"] for p in real]), "publication_periodicity": j([p["detail"] for p in real if p["detail"]])}
