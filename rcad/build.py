"""Stage 3 - build: attestations + authority tables + RCTB -> the actor database.

Output (actors/out/):  rcad.sqlite   relational database with declared foreign keys
                       csv/*.csv     the same tables, one file each
The database is rebuilt from scratch on every run; nothing is edited in it by hand.
Everything in it can be traced back: every attestation row carries the workbook, sheet and row it came
from, and its archival sources; every source page links to the RCTB document that contains it.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pandas as pd

from rctb.normalize import fold, given_compatible, parse_person
from rctb.util import Config, read_jsonl

from .clubs import auto_statements, load_statements, periods, summarise
from .ingest import REF
from .web import link
from .resolve import given_tokens, read_csv_any, s

SCHEMA = """
CREATE TABLE club (club_id TEXT PRIMARY KEY, name TEXT, other_names TEXT, charter_number TEXT, country TEXT,
                   place_id TEXT REFERENCES place(place_id), city_today TEXT, city_today_zh TEXT, province_today TEXT, province_today_zh TEXT,
                   organized TEXT, chartered TEXT, admitted TEXT, terminated TEXT,
                   meeting_day TEXT, meeting_time TEXT, meeting_place TEXT, languages TEXT,
                   publication TEXT, publication_title TEXT, publication_periodicity TEXT,
                   archive_folders TEXT, n_folders INTEGER, pages INTEGER, folders_with_page_count INTEGER, has_actor_data INTEGER);
CREATE TABLE club_statement (club_id TEXT REFERENCES club(club_id), attribute TEXT, value TEXT, detail TEXT, date_from TEXT, date_to TEXT,
                             source TEXT, status TEXT, note TEXT);
CREATE TABLE club_period (club_id TEXT REFERENCES club(club_id), period INTEGER, start TEXT, end TEXT);
CREATE TABLE club_fact (club_id TEXT REFERENCES club(club_id), fact TEXT, origin TEXT);
CREATE TABLE place (place_id TEXT PRIMARY KEY, name TEXT, modern_name TEXT, modern_name_zh TEXT, province TEXT, province_zh TEXT, kind TEXT,
                    latitude REAL, longitude REAL, note TEXT);
CREATE TABLE person (person_id TEXT PRIMARY KEY, display_name TEXT, family_name TEXT, given_name TEXT, vernacular_name TEXT,
                     nicknames TEXT, clubs TEXT, first_attested TEXT, last_attested TEXT, n_attestations INTEGER, n_documents INTEGER);
CREATE TABLE person_name (person_id TEXT REFERENCES person(person_id), club_id TEXT REFERENCES club(club_id), surname TEXT, given TEXT,
                          vernacular_name TEXT, status TEXT, rule TEXT, suggested_id TEXT, n_attestations INTEGER);
CREATE TABLE person_redirect (old_person_id TEXT PRIMARY KEY, person_id TEXT REFERENCES person(person_id));
CREATE TABLE institution (institution_id TEXT PRIMARY KEY, name TEXT, vernacular_name TEXT, other_names TEXT, n_attestations INTEGER);
CREATE TABLE position (position_id TEXT PRIMARY KEY, label TEXT, domain TEXT, category TEXT);
CREATE TABLE committee (committee_id TEXT PRIMARY KEY, name TEXT, other_names TEXT);
CREATE TABLE classification (classification TEXT PRIMARY KEY, vernacular TEXT, codes_seen TEXT, major TEXT, minor TEXT, status TEXT);
CREATE TABLE document (doc_id TEXT PRIMARY KEY, file_number TEXT, start_page INTEGER, end_page INTEGER, genre TEXT, title TEXT,
                       date_produced TEXT, summary TEXT);
CREATE TABLE source (source_id TEXT PRIMARY KEY, type TEXT, file_number TEXT, page INTEGER, doc_id TEXT REFERENCES document(doc_id),
                     citation TEXT);
CREATE TABLE membership_attestation (
    att_id TEXT PRIMARY KEY, person_id TEXT REFERENCES person(person_id), club_id TEXT REFERENCES club(club_id),
    name_as_written TEXT, date_start TEXT, date_end TEXT, election_date TEXT, charter_member INTEGER,
    classification TEXT REFERENCES classification(classification), classification_no TEXT,
    institution_id TEXT REFERENCES institution(institution_id), institution_as_written TEXT, position_as_written TEXT,
    business_address TEXT, private_address TEXT, private_address_vernacular TEXT, nickname TEXT,
    subscribes_rotarian TEXT, former_rotarian TEXT, sources TEXT, input_file TEXT, input_sheet TEXT, input_row INTEGER);
CREATE TABLE office_attestation (
    att_id TEXT PRIMARY KEY, person_id TEXT REFERENCES person(person_id), club_id TEXT REFERENCES club(club_id),
    name_as_written TEXT, position_id TEXT REFERENCES position(position_id), committee_id TEXT REFERENCES committee(committee_id),
    position_as_written TEXT, body_as_written TEXT, date_start TEXT, date_end TEXT, election_date TEXT,
    sources TEXT, input_file TEXT, input_sheet TEXT, input_row INTEGER);
CREATE TABLE club_stat (att_id TEXT, club_id TEXT REFERENCES club(club_id), date TEXT, measure TEXT, value REAL, sources TEXT,
                        input_file TEXT, input_sheet TEXT, input_row INTEGER);
CREATE TABLE attestation_source (att_id TEXT, source_id TEXT REFERENCES source(source_id));
CREATE TABLE membership (person_id TEXT REFERENCES person(person_id), club_id TEXT REFERENCES club(club_id), first_attested TEXT,
                         last_attested TEXT, election_date TEXT, charter_member INTEGER, classifications TEXT, institutions TEXT,
                         n_attestations INTEGER);
CREATE TABLE note (note_id TEXT PRIMARY KEY, club_id TEXT REFERENCES club(club_id), file_number TEXT, pages_as_written TEXT,
                   page_start INTEGER, page_end INTEGER, text TEXT, input_file TEXT);
CREATE TABLE note_document (note_id TEXT REFERENCES note(note_id), doc_id TEXT REFERENCES document(doc_id));
CREATE TABLE person_document (person_id TEXT REFERENCES person(person_id), doc_id TEXT REFERENCES document(doc_id),
                              name_in_document TEXT, role_in_document TEXT, rule TEXT);
CREATE TABLE web_page (source_id TEXT PRIMARY KEY REFERENCES source(source_id), url TEXT, title TEXT, kind TEXT, published TEXT,
                       fetched_at TEXT, text_from TEXT, pdf_url TEXT, n_statements INTEGER);
CREATE TABLE web_subject (subject_id TEXT PRIMARY KEY, type TEXT, name TEXT, vernacular TEXT, n_statements INTEGER, n_pages INTEGER,
                          match_id TEXT, status TEXT, rule TEXT);
CREATE TABLE web_statement (
    statement_id TEXT PRIMARY KEY, source_id TEXT REFERENCES web_page(source_id), pdf_page INTEGER,
    subject_id TEXT REFERENCES web_subject(subject_id), person_id TEXT REFERENCES person(person_id), club_id TEXT REFERENCES club(club_id),
    subject TEXT, subject_vernacular TEXT, subject_type TEXT, attribute TEXT, value TEXT, value_vernacular TEXT, detail TEXT,
    date_from TEXT, date_to TEXT, place TEXT, quote TEXT, reliability TEXT, model TEXT);
CREATE INDEX i_ws_person ON web_statement(person_id);
CREATE INDEX i_ma_person ON membership_attestation(person_id);
CREATE INDEX i_oa_person ON office_attestation(person_id);
CREATE INDEX i_as_att ON attestation_source(att_id);
"""


# Club cities: historical name -> (modern name, latitude, longitude). Approximate city-centre coordinates,
# used only to seed actors/authority/places.csv, where they are marked "to verify" and can be corrected.
GAZETTEER = {
    "amoy": ("Xiamen", 24.48, 118.09), "canton": ("Guangzhou", 23.13, 113.26), "changsha": ("Changsha", 28.23, 112.94),
    "chengtu": ("Chengdu", 30.57, 104.07), "chinkiang": ("Zhenjiang", 32.19, 119.45), "chungking": ("Chongqing", 29.56, 106.55),
    "dairen": ("Dalian", 38.91, 121.61), "foochow": ("Fuzhou", 26.07, 119.30), "hangchow": ("Hangzhou", 30.27, 120.15),
    "hankow": ("Wuhan (Hankou)", 30.58, 114.27), "harbin": ("Harbin", 45.80, 126.53), "hsinking": ("Changchun", 43.82, 125.32),
    "kaifeng": ("Kaifeng", 34.80, 114.31), "kunming": ("Kunming", 25.04, 102.71), "kweilin": ("Guilin", 25.27, 110.29),
    "kweiyang": ("Guiyang", 26.65, 106.63), "lanchow": ("Lanzhou", 36.06, 103.83), "liuchow": ("Liuzhou", 24.33, 109.41),
    "mukden": ("Shenyang", 41.80, 123.43), "nanchang": ("Nanchang", 28.68, 115.86), "nanking": ("Nanjing", 32.06, 118.80),
    "ningpo": ("Ningbo", 29.87, 121.55), "peking": ("Beijing", 39.90, 116.40), "shanghai": ("Shanghai", 31.23, 121.47),
    "sian": ("Xi'an", 34.34, 108.94), "soochow": ("Suzhou", 31.30, 120.59), "swatow": ("Shantou", 23.35, 116.68),
    "tientsin": ("Tianjin", 39.13, 117.20), "tsinan": ("Jinan", 36.65, 117.12), "tsingtao": ("Qingdao", 36.07, 120.38),
    "wuchang": ("Wuhan (Wuchang)", 30.54, 114.31), "wuchow": ("Wuzhou", 23.48, 111.28), "wuhu": ("Wuhu", 31.33, 118.38),
    "wusih": ("Wuxi", 31.57, 120.30),
}

# Today's city name in characters and today's province (pinyin, characters); same use and same caveat as GAZETTEER.
TODAY = {
    "amoy": ("厦门", "Fujian", "福建"), "canton": ("广州", "Guangdong", "广东"), "changsha": ("长沙", "Hunan", "湖南"),
    "chengtu": ("成都", "Sichuan", "四川"), "chinkiang": ("镇江", "Jiangsu", "江苏"), "chungking": ("重庆", "Chongqing (municipality)", "重庆市"),
    "dairen": ("大连", "Liaoning", "辽宁"), "foochow": ("福州", "Fujian", "福建"), "hangchow": ("杭州", "Zhejiang", "浙江"),
    "hankow": ("武汉（汉口）", "Hubei", "湖北"), "harbin": ("哈尔滨", "Heilongjiang", "黑龙江"), "hsinking": ("长春", "Jilin", "吉林"),
    "kaifeng": ("开封", "Henan", "河南"), "kunming": ("昆明", "Yunnan", "云南"), "kweilin": ("桂林", "Guangxi", "广西"),
    "kweiyang": ("贵阳", "Guizhou", "贵州"), "lanchow": ("兰州", "Gansu", "甘肃"), "liuchow": ("柳州", "Guangxi", "广西"),
    "mukden": ("沈阳", "Liaoning", "辽宁"), "nanchang": ("南昌", "Jiangxi", "江西"), "nanking": ("南京", "Jiangsu", "江苏"),
    "ningpo": ("宁波", "Zhejiang", "浙江"), "peking": ("北京", "Beijing (municipality)", "北京市"), "shanghai": ("上海", "Shanghai (municipality)", "上海市"),
    "sian": ("西安", "Shaanxi", "陕西"), "soochow": ("苏州", "Jiangsu", "江苏"), "swatow": ("汕头", "Guangdong", "广东"),
    "tientsin": ("天津", "Tianjin (municipality)", "天津市"), "tsinan": ("济南", "Shandong", "山东"), "tsingtao": ("青岛", "Shandong", "山东"),
    "wuchang": ("武汉（武昌）", "Hubei", "湖北"), "wuchow": ("梧州", "Guangxi", "广西"), "wuhu": ("芜湖", "Anhui", "安徽"),
    "wusih": ("无锡", "Jiangsu", "江苏"),
}
PLACE_COLS = ["place_id", "name", "modern_name", "modern_name_zh", "province", "province_zh", "kind", "latitude", "longitude", "note"]


def city_of(club_name: str) -> str:
    """'Shanghai West' -> 'shanghai': the club's city, when the name starts with a known one."""
    first = fold(club_name).split()[0] if fold(club_name) else ""
    return first if first in GAZETTEER else slug(club_name)


def slug(x: str) -> str:
    return re.sub(r"\s+", "-", fold(x))


def nn(v):
    """pandas NaN / empty -> None"""
    return None if v is None or (isinstance(v, float) and v != v) or v == "" else v


def run(base: Path, cfg: Config) -> None:
    work, auth, out = base / "work", base / "authority", base / "out"
    (out / "csv").mkdir(parents=True, exist_ok=True)
    T: dict[str, list[dict]] = {}

    # ---- clubs: one per club of the archive index (folders of the same club are grouped)
    files = read_jsonl(cfg.work / "files.jsonl")
    groups: dict[tuple, list[dict]] = {}
    for f in files:
        groups.setdefault((re.sub(r"\s+club roster$", "", f["club"], flags=re.I), f["country"]), []).append(f)   # a roster folder belongs to its club
    names = [k[0] for k in groups]
    clubs, club_of_file, club_by_name, files_of = [], {}, {}, {}
    for (name, country), fs in groups.items():
        cid = slug(name) if names.count(name) == 1 else slug(f"{name} {country}")
        first = lambda k: next((f[k] for f in fs if f.get(k)), None)
        counts = [f.get("n_pages") or f.get("index_pages") for f in fs]   # pages counted in the scans, else the index's figure
        clubs.append({"club_id": cid, "name": name, "country": country, "archive_folders": ", ".join(f["file_number"] for f in fs),
                      "n_folders": len(fs), "pages": int(sum(c for c in counts if c)) if any(counts) else None,
                      "folders_with_page_count": sum(1 for c in counts if c), "place_id": city_of(name), "has_actor_data": 0})
        files_of[cid] = fs
        for f in fs:
            club_of_file[f["file_number"]] = cid
        club_by_name.setdefault(fold(name), cid)
    club_id = lambda stem: club_by_name.get(fold(stem), slug(stem))

    # ---- places: seeded with the club cities; coordinates are yours to fill in actors/authority/places.csv
    ppath = auth / "places.csv"
    places = {r["place_id"]: r for r in read_csv_any(ppath).to_dict("records")} if ppath.exists() else {}
    for c in clubs:
        g = GAZETTEER.get(c["place_id"])
        places.setdefault(c["place_id"], {"place_id": c["place_id"], "name": c["name"].split()[0] if g else c["name"],
                                          "modern_name": g[0] if g else "", "kind": "city", "latitude": g[1] if g else "",
                                          "longitude": g[2] if g else "", "note": "approximate city centre, to verify" if g else ""})
        row = places[c["place_id"]]
        for k, v in zip(("modern_name_zh", "province", "province_zh"), TODAY.get(c["place_id"], ())):   # filled once; your values are kept
            if not nn(row.get(k)):
                row[k] = v
        if g and not nn(row.get("latitude")) and not nn(row.get("longitude")):   # an older file without coordinates: fill them once
            row.update(modern_name=row.get("modern_name") or g[0], latitude=g[1], longitude=g[2], note=row.get("note") or "approximate city centre, to verify")
    pd.DataFrame(places.values(), columns=PLACE_COLS) \
        .sort_values("place_id").to_csv(ppath, index=False, encoding="utf-8-sig")
    T["place"] = [{**{k: nn(p.get(k)) for k in PLACE_COLS}, "latitude": float(p["latitude"]) if nn(p.get("latitude")) else None,
                   "longitude": float(p["longitude"]) if nn(p.get("longitude")) else None} for p in places.values()]

    # ---- RCTB documents (if the text database has been built)
    rctb, page_doc, T["document"] = cfg.out / "rctb.sqlite", {}, []
    ents = pd.DataFrame()
    if rctb.exists():
        con = sqlite3.connect(rctb)
        T["document"] = pd.read_sql("SELECT doc_id, file_number, start_page, end_page, genre, title, date_produced, summary FROM documents", con).to_dict("records")
        page_doc = {(f, p): d for f, p, d in con.execute("SELECT file_number, page, doc_id FROM pages WHERE doc_id IS NOT NULL")}
        ents = pd.read_sql("SELECT e.doc_id, e.name, e.role, d.file_number FROM entities e JOIN documents d USING (doc_id) WHERE e.type = 'persons'", con)
        con.close()

    # ---- sources: every archive page cited in a workbook
    sources, att_src = {}, []
    coll = cfg["project"]["collection_id"]

    def cite(att_id, refs):
        ids = []
        for fn, page in REF.findall(s(refs)):
            m = re.fullmatch(r"B(\d+)-F(\d+)", fn)
            fn = f"B{int(m.group(1)):02d}-F{int(m.group(2)):02d}"
            sid = f"{fn}-p{int(page):03d}"
            sources.setdefault(sid, {"source_id": sid, "type": "archival", "file_number": fn, "page": int(page),
                                     "doc_id": page_doc.get((fn, int(page))),
                                     "citation": f"{coll}, Box {int(m.group(1))}, Folder {int(m.group(2))}, p. {int(page)}"})
            att_src.append({"att_id": att_id, "source_id": sid})
            ids.append(sid)
        return ", ".join(ids)

    # ---- authority tables
    persons = read_csv_any(auth / "persons.csv")
    pid = {(r["club"], r["surname"], r["given"]): r["person_id"] for r in persons.to_dict("records")}
    inst = read_csv_any(auth / "institutions.csv")
    iid = dict(zip(inst["name"], inst["institution_id"]))
    pmap = {(r["position"], r["body"]): r for r in read_csv_any(auth / "position_map.csv").to_dict("records")}
    T["position"] = [{"position_id": r["position_id"], "label": r["label"], "domain": r["domain"], "category": r["group"]}
                     for r in read_csv_any(auth / "positions.csv").to_dict("records")]
    com = read_csv_any(auth / "committees.csv")
    T["committee"] = [{"committee_id": c, "name": g["canonical_name"].iloc[0], "other_names": "; ".join(sorted(set(g["name"]) - {g["canonical_name"].iloc[0]}))}
                      for c, g in com.groupby("committee_id")]
    T["classification"] = [{k: nn(r.get(k)) for k in ("classification", "vernacular", "codes_seen", "major", "minor", "status")}
                           for r in read_csv_any(auth / "classifications.csv").to_dict("records")]
    known_class = {r["classification"] for r in T["classification"]}

    # ---- attestations
    rd = lambda name: pd.read_csv(work / name, dtype=str)   # everything as text: no 1931 -> 1931.0
    mem, off, stat, notes = rd("att_membership.csv"), rd("att_office.csv"), rd("att_club_stat.csv"), rd("note.csv")
    name_of = lambda r: ", ".join(x for x in (s(r["surname"]), s(r["given"])) if x)
    T["membership_attestation"] = [{
        "att_id": r["att_id"], "person_id": pid.get((r["club"], s(r["surname"]), s(r["given"]))), "club_id": club_id(r["club"]),
        "name_as_written": name_of(r), "date_start": nn(r["date_start"]), "date_end": nn(r["date_end"]), "election_date": nn(r["election_date"]),
        "charter_member": int(r["charter_member"] == "True"), "classification": r["classification"] if r["classification"] in known_class else None,
        "classification_no": nn(r["classification_no"]) and str(r["classification_no"]).replace(".0", ""),
        "institution_id": iid.get(r["institution"]), "institution_as_written": nn(r["institution"]), "position_as_written": nn(r["position"]),
        "business_address": nn(r["business_address"]), "private_address": nn(r["private_address"]),
        "private_address_vernacular": nn(r["private_address_vernacular"]), "nickname": nn(r["nickname"]),
        "subscribes_rotarian": nn(r["subscribes_rotarian"]), "former_rotarian": nn(r["former_rotarian"]),
        "sources": cite(r["att_id"], r["source_refs"]), "input_file": r["input_file"], "input_sheet": r["input_sheet"],
        "input_row": int(r["input_row"])} for r in mem.to_dict("records") if nn(r["surname"])]
    T["office_attestation"] = []
    for r in off.to_dict("records"):
        if not nn(r["surname"]):
            continue
        m = pmap.get((s(r["position"]), s(r["body"])), {})
        T["office_attestation"].append({
            "att_id": r["att_id"], "person_id": pid.get((r["club"], s(r["surname"]), s(r["given"]))), "club_id": club_id(r["club"]),
            "name_as_written": name_of(r), "position_id": nn(m.get("position_id")), "committee_id": nn(m.get("committee_id")),
            "position_as_written": nn(r["position"]), "body_as_written": nn(r["body"]), "date_start": nn(r["date_start"]),
            "date_end": nn(r["date_end"]), "election_date": nn(r["election_date"]), "sources": cite(r["att_id"], r["source_refs"]),
            "input_file": r["input_file"], "input_sheet": r["input_sheet"], "input_row": int(r["input_row"])})
    T["club_stat"] = [{"att_id": r["att_id"], "club_id": club_id(r["club"]), "date": nn(r["date"]), "measure": r["measure"], "value": float(r["value"]),
                       "sources": cite(r["att_id"], r["source_refs"]), "input_file": r["input_file"], "input_sheet": r["input_sheet"],
                       "input_row": int(r["input_row"])} for r in stat.to_dict("records")]
    T["source"], T["attestation_source"] = list(sources.values()), att_src

    # ---- notes, linked to the documents on the pages they describe
    T["note"] = [{"note_id": r["note_id"], "club_id": club_id(r["club"]), "file_number": r["file_number"], "pages_as_written": nn(r["pages_as_written"]),
                  "page_start": nn(r["page_start"]) and int(float(r["page_start"])), "page_end": nn(r["page_end"]) and int(float(r["page_end"])), "text": r["text"], "input_file": r["input_file"]}
                 for r in notes.to_dict("records")]
    T["note_document"] = [{"note_id": n["note_id"], "doc_id": d} for n in T["note"] if n["page_start"]
                          for d in dict.fromkeys(page_doc.get((n["file_number"], p)) for p in range(int(n["page_start"]), int(n["page_end"]) + 1)) if d]
    hdr = pd.read_csv(work / "club_header.csv", dtype=str) if (work / "club_header.csv").exists() else pd.DataFrame()
    T["club_fact"] = [{"club_id": club_id(r["club"]), "fact": r["line"], "origin": r["input_file"]} for r in hdr.to_dict("records")]

    # ---- persons and institutions (from the authority tables), with what the attestations say about them
    ma, oa = pd.DataFrame(T["membership_attestation"]), pd.DataFrame(T["office_attestation"])
    both = pd.concat([ma[["person_id", "club_id", "date_start", "date_end"]], oa[["person_id", "club_id", "date_start", "date_end"]]])
    T["person_name"] = [{"person_id": r["person_id"], "club_id": club_id(r["club"]), "surname": r["surname"], "given": nn(r["given"]),
                         "vernacular_name": nn(r["vernacular_name"]), "status": r["status"], "rule": nn(r["rule"]),
                         "suggested_id": nn(r["suggested_id"]), "n_attestations": int(r["n_attestations"] or 0)} for r in persons.to_dict("records")]

    # documents of the text database that name a person: family name + compatible initials, within the same club
    variants: dict[str, list[tuple]] = {}
    for r in persons.to_dict("records"):
        variants.setdefault(r["person_id"], []).append((club_id(r["club"]), fold(r["surname"]), given_tokens(r["given"])))
    links = []
    for e in ents.to_dict("records"):
        fam, giv = parse_person(e["name"])
        club = club_of_file.get(e["file_number"])
        cands = [p for p, vs in variants.items() if any(c == club and f == fam for c, f, _ in vs)]
        if giv:
            hit = [p for p in cands if any(c == club and f == fam and g and given_compatible(giv, g) for c, f, g in variants[p])]
            rule = "family name and given name or initials"
        else:
            hit, rule = cands, "family name only (single person of that name in the club)"
        if len(hit) == 1:
            links.append({"person_id": hit[0], "doc_id": e["doc_id"], "name_in_document": e["name"], "role_in_document": nn(e["role"]), "rule": rule})
    T["person_document"] = pd.DataFrame(links, columns=["person_id", "doc_id", "name_in_document", "role_in_document", "rule"]) \
        .drop_duplicates(["person_id", "doc_id"]).to_dict("records")
    ndocs = pd.Series([l["person_id"] for l in T["person_document"]]).value_counts().to_dict()

    T["person"] = []
    for p, g in persons.groupby("person_id"):
        best = g.loc[g["given"].map(lambda x: len(fold(x))).idxmax()]
        b = both[both["person_id"] == p]
        dates = sorted(str(d) for d in pd.concat([b["date_start"], b["date_end"]]).dropna())
        nick = sorted(set(ma.loc[ma["person_id"] == p, "nickname"].dropna())) if len(ma) else []
        T["person"].append({"person_id": p, "display_name": g["display_name"].iloc[0], "family_name": best["surname"], "given_name": nn(best["given"]),
                            "vernacular_name": next((v for v in g["vernacular_name"] if nn(v)), None), "nicknames": "; ".join(nick) or None,
                            "clubs": ", ".join(sorted(set(b["club_id"]))) or None, "first_attested": dates[0] if dates else None,
                            "last_attested": dates[-1] if dates else None, "n_attestations": int(len(b)), "n_documents": int(ndocs.get(p, 0))})
    T["institution"] = [{"institution_id": i, "name": g["canonical_name"].iloc[0], "vernacular_name": next((v for v in g["vernacular_name"] if nn(v)), None),
                         "other_names": "; ".join(sorted(set(g["name"]) - {g["canonical_name"].iloc[0]})) or None,
                         "n_attestations": int(pd.to_numeric(g["n_attestations"]).sum())} for i, g in inst.groupby("institution_id")]

    # ---- membership: one line per person and club, summarising the attestations (never edited; recomputed)
    T["membership"] = []
    if len(ma):
        inst_name = {r["institution_id"]: r["name"] for r in T["institution"]}
        for (p, c), g in ma[ma["person_id"].notna()].groupby(["person_id", "club_id"]):
            o = oa[(oa["person_id"] == p) & (oa["club_id"] == c)]
            dates = sorted(str(d) for d in pd.concat([g["date_start"], g["date_end"], o["date_start"], o["date_end"]]).dropna())
            el = sorted(g["election_date"].dropna())
            T["membership"].append({"person_id": p, "club_id": c, "first_attested": dates[0] if dates else None, "last_attested": dates[-1] if dates else None,
                                    "election_date": el[0] if el else None, "charter_member": int(g["charter_member"].max()),
                                    "classifications": "; ".join(dict.fromkeys(g["classification"].dropna())) or None,
                                    "institutions": "; ".join(dict.fromkeys(inst_name[i] for i in g["institution_id"].dropna())) or None,
                                    "n_attestations": len(g) + len(o)})
    with_data = set(both["club_id"].dropna())
    for c in clubs:
        c["has_actor_data"] = int(c["club_id"] in with_data)
    known_clubs = {c["club_id"] for c in clubs}
    clubs += [{"club_id": c, "name": c, "has_actor_data": 1, "place_id": None} for c in sorted(with_data - known_clubs)]  # workbook without index entry

    # ---- club identification: statements with their sources (index + notes header + your own rows), then one summary line per club
    st = load_statements(auth / "club_statements.csv", auto_statements(clubs, files_of, hdr.to_dict("records"), club_id))
    unknown = sorted(set(st["club_id"]) - {c["club_id"] for c in clubs})
    if unknown:
        print(f"       club_statements.csv: unknown club_id {unknown} (rows left out)")
    st = st[~st["club_id"].isin(unknown)]
    T["club_statement"], T["club_period"] = st.to_dict("records"), periods(st)
    for c in clubs:
        pl = places.get(c["place_id"], {})
        c.update(summarise(st, c["club_id"]), city_today=nn(pl.get("modern_name")), city_today_zh=nn(pl.get("modern_name_zh")),
                 province_today=nn(pl.get("province")), province_today_zh=nn(pl.get("province_zh")))
    T["club"] = clubs

    # ---- statements read from rotaryinchina.org (stage `web`), labelled secondary; each page is also a source
    T["web_page"], T["web_subject"], T["web_statement"] = link(base, clubs, pd.DataFrame(T["person_name"]))
    T["web_statement"] = [{**r, "person_id": r["person_id"] if r["person_id"] in set(persons["person_id"]) else None} for r in T["web_statement"]]
    T["source"] += [{"source_id": w["source_id"], "type": "web", "file_number": None, "page": None, "doc_id": None,
                     "citation": f"{w['title']}, rotaryinchina.org, {w['url']} (consulted {w['fetched_at'][:10]})"} for w in T["web_page"]]

    # ---- permanent identifiers: remember every (name variant -> person) ever published; a person_id that has since
    #      been merged into another keeps resolving through person_redirect
    hpath = auth / "person_id_history.csv"
    hist = read_csv_any(hpath)
    now = persons[["club", "surname", "given", "person_id"]]
    hist = pd.concat([hist, now]).drop_duplicates() if len(hist) else now
    hist.to_csv(hpath, index=False, encoding="utf-8-sig")
    current = set(persons["person_id"])
    cur_of = {(r["club"], r["surname"], r["given"]): r["person_id"] for r in persons.to_dict("records")}
    T["person_redirect"] = [{"old_person_id": old, "person_id": new} for old, new in
                            {r["person_id"]: cur_of.get((r["club"], r["surname"], s(r["given"]))) for r in hist.to_dict("records")
                             if r["person_id"] not in current}.items() if new]

    # ---- write
    db = out / "rcad.sqlite"
    db.unlink(missing_ok=True)
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    for name in re.findall(r"CREATE TABLE (\w+)", SCHEMA):
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({name})")]
        df = pd.DataFrame(T.get(name, []), columns=cols)
        df = df.astype(object).where(df.notna(), None)
        con.executemany(f"INSERT INTO {name} VALUES ({','.join('?' * len(cols))})", df.values.tolist())
        df.to_csv(out / "csv" / f"{name}.csv", index=False, encoding="utf-8-sig")
    con.executescript("""
        CREATE VIRTUAL TABLE note_fts USING fts5(text, pages_as_written, content="note", content_rowid="rowid");
        INSERT INTO note_fts(rowid, text, pages_as_written) SELECT rowid, text, pages_as_written FROM note;
        CREATE VIRTUAL TABLE person_fts USING fts5(display_name, vernacular_name, nicknames, content="person", content_rowid="rowid");
        INSERT INTO person_fts(rowid, display_name, vernacular_name, nicknames) SELECT rowid, display_name, vernacular_name, nicknames FROM person;
        CREATE VIEW office AS SELECT o.person_id, p.display_name, o.club_id, pos.label AS position, c.name AS committee,
               o.date_start, o.date_end, o.sources, o.att_id FROM office_attestation o
               LEFT JOIN person p USING (person_id) LEFT JOIN position pos USING (position_id) LEFT JOIN committee c USING (committee_id);
    """)
    problems = con.execute("PRAGMA foreign_key_check").fetchall()
    con.commit()
    con.close()

    # ---- the same data as a graph: one file of nodes, one of edges, for Gephi, igraph / networkx or a graph database
    (out / "graph").mkdir(exist_ok=True)
    nodes = ([{"id": r["person_id"], "label": r["display_name"], "type": "person"} for r in T["person"]]
             + [{"id": r["club_id"], "label": r["name"], "type": "club"} for r in T["club"] if r["has_actor_data"]]
             + [{"id": r["institution_id"], "label": r["name"], "type": "institution"} for r in T["institution"]]
             + [{"id": r["committee_id"], "label": r["name"], "type": "committee"} for r in T["committee"]])
    pos_label = {r["position_id"]: r["label"] for r in T["position"]}
    raw = ([(r["person_id"], r["club_id"], "member_of", None, r["date_start"], r["date_end"], r["sources"]) for r in T["membership_attestation"]]
           + [(r["person_id"], r["institution_id"], "affiliated_with", r["position_as_written"], r["date_start"], r["date_end"], r["sources"])
              for r in T["membership_attestation"] if r["institution_id"]]
           + [(r["person_id"], r["committee_id"] or r["club_id"], "holds_office", pos_label.get(r["position_id"], r["position_as_written"]),
               r["date_start"], r["date_end"], r["sources"]) for r in T["office_attestation"]])
    edges = pd.DataFrame([x for x in raw if x[0] and x[1]], columns=["source", "target", "relation", "role", "date_start", "date_end", "sources"])
    if len(edges):   # one edge per (who, what, relation, role), with the period and the sources of all its attestations
        edges = edges.fillna("")
        edges["date_last"] = edges[["date_start", "date_end"]].max(axis=1)
        edges = edges.groupby(["source", "target", "relation", "role"], as_index=False).agg(
            first_attested=("date_start", lambda x: min([v for v in x if v], default="")),
            last_attested=("date_last", "max"),
            n_attestations=("sources", "size"),
            archive_pages=("sources", lambda x: ", ".join(sorted({p for v in x for p in v.split(", ") if p}))))
    pd.DataFrame(nodes).to_csv(out / "graph" / "nodes.csv", index=False, encoding="utf-8-sig")
    edges.to_csv(out / "graph" / "edges.csv", index=False, encoding="utf-8-sig")

    n = {k: len(v) for k, v in T.items()}
    unlinked = sum(1 for x in T["source"] if x["type"] == "archival" and not x["doc_id"])
    print(f"build: {n['person']} persons, {n['institution']} institutions, {n['club']} clubs ({len(with_data)} with actor data), "
          f"{n['membership_attestation']} membership + {n['office_attestation']} office attestations, {n['club_stat']} statistics, "
          f"{n['note']} notes\n       {sum(1 for x in T["source"] if x["type"] == "archival")} archive pages cited, of which {sum(1 for x in T["source"] if x["type"] == "archival") - unlinked} linked to an RCTB document "
          f"({unlinked} on pages not yet in the text database); {n['note_document']} note-document links; "
          f"{n['person_document']} person-document links from the text database's entities\n       -> {db}\n       graph export: {len(nodes)} nodes, {len(edges)} edges -> {out / 'graph'}"
          + (f"\n       WARNING: {len(problems)} broken references (foreign_key_check)" if problems else ""))
    no_person = sum(1 for r in T["membership_attestation"] + T["office_attestation"] if not r["person_id"])
    if no_person:
        print(f"       {no_person} attestations have no person yet: run `resolve` before `build`.")
