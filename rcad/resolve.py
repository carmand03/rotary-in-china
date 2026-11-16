"""Stage 2 - resolve: who is who, and what the controlled vocabularies are.

Attestations keep names as written ("Kowasaki, Torao", "Kowazaki, Torao", "Kawasaki, T."). This stage
maintains the authority tables in actors/authority/ that say which variants are the same person,
institution, committee, position or classification. They are plain CSV files that you edit.

Identifiers are permanent. Once a name variant has received a person_id (or institution_id), the
pipeline never changes it; only you do, by editing the CSV. New variants found later are attached to
an existing person when the evidence is strong (status `auto`), otherwise they receive an identifier of
their own and the likely match is proposed in `suggested_id` (status `suggested`).

  status   auto       assigned by a rule (column `rule` says which); you may overrule it
           suggested  kept separate for now; `suggested_id` names the probable match
           confirmed  your decision (set this after editing an identifier)
           rejected   your decision: the proposed match is wrong; do not propose it again
To merge two persons: give both rows the same person_id and set status to `confirmed`.
"""
from __future__ import annotations

import csv
import difflib
import re
from pathlib import Path

import pandas as pd

from rctb.normalize import fold, given_compatible, other_key

CJK = re.compile(r"[぀-ヿ㐀-鿿]")


def read_csv_any(path: Path) -> pd.DataFrame:
    """A CSV as saved by any spreadsheet program (comma / semicolon / tab; UTF-8 or Windows / Mac encodings)."""
    if not path.exists():
        return pd.DataFrame()
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "mac_roman"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    first = text.splitlines()[0] if text.strip() else ""
    delim = max(",;\t", key=first.count)
    rows = list(csv.DictReader(text.splitlines(), delimiter=delim))
    df = pd.DataFrame([{(k or "").strip(): (v or "").strip() for k, v in r.items() if k} for r in rows])
    if "status" in df:
        df["status"] = df["status"].str.lower()
    return df


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False, encoding="utf-8-sig")


def next_id(prefix: str, used) -> callable:
    n = max([int(re.sub(r"\D", "", str(u)) or 0) for u in used if str(u).startswith(prefix)] + [0])

    def make():
        nonlocal n
        n += 1
        return f"{prefix}{n:05d}"
    return make


def s(v) -> str:
    return "" if v is None or (isinstance(v, float) and v != v) else str(v)


def given_tokens(given: str) -> list[str]:
    return fold(given).split()


# ------------------------------------------------------------------ persons
PERSON_COLS = ["person_id", "club", "surname", "given", "vernacular_name", "display_name", "status", "rule", "suggested_id",
               "n_attestations", "years", "roles", "note"]


def resolve_persons(base: Path) -> pd.DataFrame:
    work, path = base / "work", base / "authority" / "persons.csv"
    mem, off = pd.read_csv(work / "att_membership.csv", dtype=str), pd.read_csv(work / "att_office.csv", dtype=str)
    both = pd.concat([mem.assign(kind="member"), off.assign(kind="officer")], ignore_index=True)
    both = both[both["surname"].notna()]
    variants = {}
    for (club, sur, giv), g in both.groupby(["club", both["surname"].map(s), both["given"].map(s)]):
        years = sorted({str(d)[:4] for d in pd.concat([g["date_start"], g["date_end"]]).dropna()})
        variants[(club, sur, giv)] = {
            "vernaculars": sorted({v for v in g["vernacular_name"].dropna() if CJK.search(str(v))}),
            "n": len(g), "years": f"{years[0]}-{years[-1]}" if years else "",
            "roles": ", ".join(sorted(set(g["kind"])))}

    old = read_csv_any(path)
    known = {(r["club"], r["surname"], r["given"]): r for r in old.to_dict("records")} if len(old) else {}
    new_id = next_id("P", [r["person_id"] for r in known.values()])
    persons: dict[str, list[tuple]] = {}        # person_id -> variant keys, for matching
    for key, r in known.items():
        if key in variants:
            persons.setdefault(r["person_id"], []).append(key)
    rejected = {(k, r.get("suggested_id")) for k, r in known.items() if r.get("status") == "rejected"}

    def compatible(a, b, strict=True) -> bool:
        (_, s1, g1), (_, s2, g2) = a, b
        f1, f2 = fold(s1), fold(s2)
        same_family = f1 == f2 if strict else difflib.SequenceMatcher(None, f1, f2).ratio() >= 0.8
        t1, t2 = given_tokens(g1), given_tokens(g2)
        return same_family and (given_compatible(t1, t2) if (t1 and t2) else not strict)

    rows = {}
    # most complete names first, so that "Gundsi, Yoshio" founds the person and "Gundsi, Y." joins it
    order = sorted(variants, key=lambda k: (k in known, len(fold(k[2])), variants[k]["n"]), reverse=True)
    for key in order:
        v = variants[key]
        if key in known:
            r = known[key]
            rows[key] = {**r, "n_attestations": v["n"], "years": v["years"], "roles": v["roles"],
                         "vernacular_name": r.get("vernacular_name") or "; ".join(v["vernaculars"])}
            continue
        club = key[0]
        same_club = {pid: ks for pid, ks in persons.items() if ks and ks[0][0] == club}
        by_vern = [pid for pid, ks in same_club.items()
                   if set(v["vernaculars"]) & {x for k in ks for x in variants[k]["vernaculars"]}]
        strong = [pid for pid, ks in same_club.items() if all(compatible(key, k) for k in ks)]
        weak = [pid for pid, ks in same_club.items() if pid not in strong and any(compatible(key, k, strict=False) for k in ks)]
        if len(set(by_vern)) == 1:
            pid, status, rule, sug = by_vern[0], "auto", "same name in Chinese/Japanese characters", ""
        elif len(strong) == 1 and not by_vern:
            pid, status, rule, sug = strong[0], "auto", "same family name, compatible given name or initials", ""
        else:
            pid = new_id()
            cands = [c for c in dict.fromkeys(by_vern + strong + weak) if (key, c) not in rejected]
            if cands:
                why = "several possible matches" if len(strong) > 1 or len(set(by_vern)) > 1 else "similar family name, or given name missing"
                status, rule, sug = "suggested", why, " / ".join(cands)
            else:
                status, rule, sug = "auto", "new person", ""
        persons.setdefault(pid, []).append(key)
        rows[key] = {"person_id": pid, "club": club, "surname": key[1], "given": key[2], "vernacular_name": "; ".join(v["vernaculars"]),
                     "status": status, "rule": rule, "suggested_id": sug, "n_attestations": v["n"], "years": v["years"],
                     "roles": v["roles"], "note": ""}
    # rows of the registry whose variant no longer occurs in the inputs are kept (identifiers are never reused)
    for key, r in known.items():
        rows.setdefault(key, {**r, "n_attestations": 0})
    # display name: the fullest variant of each person
    best = {}
    for key, r in rows.items():
        cand = (len(fold(key[2])), int(r["n_attestations"] or 0), f"{key[1]}, {key[2]}".strip(", "))
        if cand > best.get(r["person_id"], (-1, -1, "")):
            best[r["person_id"]] = cand
    out = []
    for key, r in rows.items():
        r["display_name"] = best[r["person_id"]][2] if r.get("status") != "confirmed" or not r.get("display_name") else r["display_name"]
        out.append(r)
    out.sort(key=lambda r: (r["club"], fold(r["surname"]), r["person_id"], fold(r["given"])))
    write_csv(path, out, PERSON_COLS)
    df = pd.DataFrame(out)
    st = df["status"].value_counts().to_dict()
    print(f"resolve: persons - {len(df)} name variants -> {df['person_id'].nunique()} persons "
          f"({st.get('suggested', 0)} suggestions to review, {st.get('confirmed', 0) + st.get('rejected', 0)} decided by you) -> {path}")
    return df


# ------------------------------------------------------------------ institutions
INST_COLS = ["institution_id", "name", "vernacular_name", "canonical_name", "status", "rule", "suggested_id", "n_attestations", "clubs", "note"]


def resolve_institutions(base: Path) -> pd.DataFrame:
    path = base / "authority" / "institutions.csv"
    mem = pd.read_csv(base / "work" / "att_membership.csv", dtype=str)
    mem = mem[mem["institution"].notna()]
    variants = {}
    for name, g in mem.groupby("institution"):
        variants[name] = {"n": len(g), "clubs": "; ".join(sorted(set(g["club"]))),
                          "vern": "; ".join(sorted({v for v in g["institution_vernacular"].dropna()}))[:200]}
    old = read_csv_any(path)
    known = {r["name"]: r for r in old.to_dict("records")} if len(old) else {}
    new_id = next_id("I", [r["institution_id"] for r in known.values()])
    by_key: dict[str, str] = {}
    for name, r in known.items():
        by_key.setdefault(other_key("organizations", name), r["institution_id"])
    rows = {}
    for name in sorted(variants, key=lambda n: (n not in known, -variants[n]["n"], n)):
        v = variants[name]
        if name in known:
            rows[name] = {**known[name], "n_attestations": v["n"], "clubs": v["clubs"],
                          "vernacular_name": known[name].get("vernacular_name") or v["vern"]}
            continue
        key = other_key("organizations", name)
        if key in by_key:
            iid, status, rule, sug = by_key[key], "auto", "same name ignoring case, punctuation and Co./Ltd.", ""
        else:
            iid = new_id()
            close = [(difflib.SequenceMatcher(None, key, k).ratio(), i) for k, i in by_key.items()]
            close = [c for c in close if c[0] >= 0.85]
            status, rule, sug = ("suggested", "similar name", max(close)[1]) if close else ("auto", "new institution", "")
            by_key[key] = iid
        rows[name] = {"institution_id": iid, "name": name, "vernacular_name": v["vern"], "status": status, "rule": rule,
                      "suggested_id": sug, "n_attestations": v["n"], "clubs": v["clubs"], "note": ""}
    for name, r in known.items():
        rows.setdefault(name, {**r, "n_attestations": 0})
    canon = {}
    for r in rows.values():
        cand = (int(r["n_attestations"] or 0), len(r["name"]), r["name"])
        if cand > canon.get(r["institution_id"], (-1, -1, "")):
            canon[r["institution_id"]] = cand
    out = [{**r, "canonical_name": r.get("canonical_name") if r.get("status") == "confirmed" and r.get("canonical_name")
            else canon[r["institution_id"]][2]} for r in rows.values()]
    out.sort(key=lambda r: (fold(r["canonical_name"]), r["institution_id"]))
    write_csv(path, out, INST_COLS)
    df = pd.DataFrame(out)
    print(f"resolve: institutions - {len(df)} names -> {df['institution_id'].nunique()} institutions "
          f"({(df['status'] == 'suggested').sum()} suggestions) -> {path}")
    return df


# ------------------------------------------------------------------ dictionaries
POSITIONS = [  # from the RCAD scheme; add rows to actors/authority/positions.csv as needed
    ("MEMBER", "Regular Member", "membership"), ("MEMBER_ASSOCIATE", "Associate/Supplementary Member", "membership"),
    ("MEMBER_HONORARY", "Honorary Member", "membership"),
    ("PRESIDENT", "President", "officers"), ("VICE_PRESIDENT", "Vice-President", "officers"), ("SECRETARY", "Secretary", "officers"),
    ("HON_SECRETARY", "Honorary Secretary", "officers"), ("TREASURER", "Treasurer", "officers"),
    ("SERGEANT_AT_ARMS", "Sergeant-At-Arms", "officers"), ("HON_TREASURER", "Honorary Treasurer", "officers"),
    ("CORR_SECRETARY", "Corresponding Secretary", "officers"), ("PAST_PRESIDENT", "Past President", "officers"),
    ("BOARD_CHAIRMAN", "Chairman, Board of Directors", "board"), ("BOARD_MEMBER", "Director", "board"),
    ("COMMITTEE_CHAIRMAN", "Committee Chairman", "committee"), ("COMMITTEE_MEMBER", "Committee Member", "committee"),
]


def resolve_dictionaries(base: Path) -> None:
    auth, work = base / "authority", base / "work"
    off, mem = pd.read_csv(work / "att_office.csv", dtype=str), pd.read_csv(work / "att_membership.csv", dtype=str)

    # positions: the list itself (yours to extend) ...
    ppath = auth / "positions.csv"
    pos = read_csv_any(ppath)
    have = set(pos["position_id"]) if len(pos) else set()
    rows = pos.to_dict("records") + [{"position_id": i, "label": l, "domain": "rotary", "group": g} for i, l, g in POSITIONS if i not in have]
    write_csv(ppath, rows, ["position_id", "label", "domain", "group"])
    labels = {fold(r["label"]): r["position_id"] for r in rows}
    labels.update({"vice president": "VICE_PRESIDENT", "sergeant at arms": "SERGEANT_AT_ARMS", "hon secretary": "HON_SECRETARY",
                   "correspondence secretary": "CORR_SECRETARY", "director": "BOARD_MEMBER"})

    def position_key(p: str) -> str:   # spelling variants of the same office
        k = fold(p)
        k = re.sub(r"^(sgt|s)\b", "sergeant", k)
        k = re.sub(r"^(honor|hon)\b", "honorary", k)
        return k

    # ... committees: every body named in the officers sheets that is not "Officers" or the board
    cpath = auth / "committees.csv"
    old = read_csv_any(cpath)
    known = {r["name"]: r for r in old.to_dict("records")} if len(old) else {}
    new_id = next_id("C", [r["committee_id"] for r in known.values()])
    key_of = lambda n: re.sub(r"\s+", " ", re.sub(r"\b(committee|the|and)\b", "", fold(n))).strip().rstrip("s")
    by_key = {}
    for n, r in known.items():
        by_key.setdefault(key_of(n), r["committee_id"])
    bodies = off["body"].dropna().value_counts()
    crow = dict(known)
    for name, n in bodies.items():
        f = fold(name)
        if f in ("officers", "officer", "club") or "board" in f:
            continue
        if name in known:
            crow[name]["n_attestations"] = int(n)
            continue
        k = key_of(name)
        if k in by_key:
            cid, status, rule = by_key[k], "auto", "same name ignoring 'Committee' and plural"
        else:
            cid, status, rule = new_id(), "auto", "new committee"
            by_key[k] = cid
        crow[name] = {"committee_id": cid, "name": name, "canonical_name": "", "status": status, "rule": rule, "n_attestations": int(n), "note": ""}
    canon = {}
    for r in crow.values():
        if (int(r.get("n_attestations") or 0), r["name"]) > canon.get(r["committee_id"], (-1, "")):
            canon[r["committee_id"]] = (int(r.get("n_attestations") or 0), r["name"])
    for r in crow.values():
        if not (r.get("status") == "confirmed" and r.get("canonical_name")):
            r["canonical_name"] = canon[r["committee_id"]][1]
    write_csv(cpath, sorted(crow.values(), key=lambda r: fold(r["canonical_name"])),
              ["committee_id", "name", "canonical_name", "status", "rule", "n_attestations", "note"])

    # ... how each (position, body) pair written in the sheets maps onto the two lists
    mpath = auth / "position_map.csv"
    old = read_csv_any(mpath)
    known_map = {(r["position"], r["body"]): r for r in old.to_dict("records")} if len(old) else {}
    mrows = dict(known_map)
    for (p, b), n in off.groupby([off["position"].map(s), off["body"].map(s)]).size().items():
        if (p, b) in known_map:
            mrows[(p, b)]["n_attestations"] = int(n)
            continue
        fp, fb = position_key(p), fold(b)
        pid, cid, status = "", "", "auto"
        if fb in ("officers", "officer", "club", ""):
            pid = labels.get(fp, "")
        elif "board" in fb:
            pid = "BOARD_CHAIRMAN" if "chair" in fp else "BOARD_MEMBER"
        else:
            pid = "COMMITTEE_CHAIRMAN" if "chair" in fp else ("COMMITTEE_MEMBER" if fp in ("member", "") else labels.get(fp, ""))
            cid = crow[b]["committee_id"] if b in crow else ""
        if not pid:
            status = "suggested"
        mrows[(p, b)] = {"position": p, "body": b, "position_id": pid, "committee_id": cid, "status": status, "n_attestations": int(n), "note": ""}
    write_csv(mpath, sorted(mrows.values(), key=lambda r: (r["body"], r["position"])),
              ["position", "body", "position_id", "committee_id", "status", "n_attestations", "note"])

    # ... classifications: every wording found, with empty major / minor columns for you to fill from the RI outline
    kpath = auth / "classifications.csv"
    old = read_csv_any(kpath)
    known_k = {r["classification"]: r for r in old.to_dict("records")} if len(old) else {}
    krows = dict(known_k)
    allc = pd.concat([mem[["classification", "classification_vernacular", "classification_no"]],
                      off[["classification"]].assign(classification_vernacular=None, classification_no=None)])
    for name, g in allc[allc["classification"].notna()].groupby("classification"):
        if name in known_k:
            krows[name]["n_attestations"] = len(g)
            continue
        krows[name] = {"classification": name, "vernacular": "; ".join(sorted(set(g["classification_vernacular"].dropna().map(str))))[:80],
                       "codes_seen": "; ".join(sorted(set(g["classification_no"].dropna().map(lambda x: str(x).replace(".0", ""))))),
                       "major": "", "minor": "", "status": "todo", "n_attestations": len(g), "note": ""}
    write_csv(kpath, sorted(krows.values(), key=lambda r: fold(r["classification"])),
              ["classification", "vernacular", "codes_seen", "major", "minor", "status", "n_attestations", "note"])
    unmapped = sum(1 for r in mrows.values() if r["status"] == "suggested")
    print(f"resolve: dictionaries - {len(rows)} positions, {len({r['committee_id'] for r in crow.values()})} committees "
          f"({len(crow)} wordings), {len(mrows)} position/body pairs ({unmapped} unmapped), {len(krows)} classification wordings "
          f"({sum(1 for r in krows.values() if not r.get('major'))} without major/minor) -> {auth}")


def run(base: Path) -> None:
    resolve_persons(base)
    resolve_institutions(base)
    resolve_dictionaries(base)
