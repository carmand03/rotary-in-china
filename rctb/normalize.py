"""Stage `normalize` - group the variants of a named entity under one canonical form.

Records keep every name exactly as written. This stage builds an authority table,
corrections/entities.csv, that maps each variant to a canonical form; `build` then adds a
`canonical` value to every entity, which is what the viewer's filter and network queries use.

No model is involved: the grouping uses explicit rules, so it is deterministic and explainable.
  status = auto       strong rule, applied           (Y. Gundsi / Yoshio Gundsi / Gundsi, Y.)
  status = suggested  weaker evidence, NOT applied   (bare surname, likely OCR variant, "Harbin, Manchuria")
  status = confirmed  set by you: applied, and never touched again by the pipeline
  status = rejected   set by you: the variant stays on its own, and is never proposed again
To decide a case, edit `canonical` and/or `status` in the CSV (Excel or any editor), then run `build`.
"""
from __future__ import annotations

import csv
import difflib
import re
import unicodedata
from collections import Counter, defaultdict

from .util import Config, load_files, log_run, read_jsonl

FIELDS = ["type", "name", "canonical", "status", "n_docs", "clubs", "rule", "note"]
TITLES = {"mr", "mrs", "miss", "dr", "prof", "professor", "rotarian", "rtn", "rev", "sir", "col", "capt", "gen",
          "president", "secretary", "governor", "hon", "messrs", "past"}
PARTICLES = {"van", "von", "de", "del", "der", "den", "di", "da", "la", "le", "du"}


def fold(s: str) -> str:
    """Case-, accent- and punctuation-insensitive form used for comparison only."""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c)).casefold()
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s)).strip()


def parse_person(name: str) -> tuple[str, list[str]]:
    """-> (family key, given-name tokens). 'Gundsi, Y.' and 'Y. Gundsi' give ('gundsi', ['y']).
    For names written family-name-first (Yen Chuan-Fu) the 'family key' is simply the last token:
    that is fine, the key only has to be the same for all variants of one name."""
    if "," in name:
        family, given = name.split(",", 1)
        fam, giv = fold(family).split(), fold(given).split()
    else:
        toks = [t for t in fold(name).split()]
        while toks and toks[0] in TITLES:
            toks = toks[1:]
        if not toks:
            return "", []
        cut = len(toks) - 1
        while cut > 0 and toks[cut - 1] in PARTICLES:
            cut -= 1
        fam, giv = toks[cut:], toks[:cut]
    giv = [g for g in giv if g not in TITLES]
    return " ".join(fam), giv


def given_compatible(a: list[str], b: list[str]) -> bool:
    """Y. ~ Yoshio; E. E. ~ E.; Koichi !~ Kenji. Compared position by position on the shared length."""
    if not a or not b:
        return False
    for x, y in zip(a, b):
        if len(x) == 1 or len(y) == 1:
            if x[0] != y[0]:
                return False
        elif x != y and difflib.SequenceMatcher(None, x, y).ratio() < 0.85:
            return False
    return True


def other_key(etype: str, name: str) -> str:
    k = fold(name)
    k = re.sub(r"^the ", "", k)
    if etype == "organizations":
        m = re.search(r"rotary club of (\w+)", k) or re.search(r"(\w+) rotary club", k)
        if m and m.group(1) not in ("the", "a"):
            return f"rotary club of {m.group(1)}"
        k = re.sub(r"\b(co|company|ltd|limited|inc|corp|corporation)\b", "", k)
        k = re.sub(r"\s+", " ", k.replace(" and ", " ")).strip()
    return k


def best_form(variants: list[str], counts: Counter, llm_norm: dict, person: bool = False) -> str:
    """Canonical form: a standard form supplied during enrichment if there is one, otherwise the
    most complete variant (longest; ties broken by frequency, then mixed case over ALL CAPS)."""
    norms = Counter(llm_norm[v] for v in variants if llm_norm.get(v))
    if norms:
        return norms.most_common(1)[0][0]
    best = max(variants, key=lambda v: (len(fold(v)), not v.isupper(), counts[v], v))
    if person and "," in best:  # "Ahnert, E. E." -> "E. E. Ahnert"
        family, given = (x.strip() for x in best.split(",", 1))
        best = f"{given} {family}".strip()
    return best


def propose(mentions: list[dict]) -> list[dict]:
    """mentions: {type, name, normalized, doc_id, club} -> one row per (type, variant)."""
    rows = []
    for etype in sorted({m["type"] for m in mentions}):
        ms = [m for m in mentions if m["type"] == etype]
        docs, clubs, llm_norm = defaultdict(set), defaultdict(set), {}
        for m in ms:
            docs[m["name"]].add(m["doc_id"])
            clubs[m["name"]].add(m["club"])
            # A standard form from the model is used for places and bodies only, and never across different numbers
            # (the 1937 and the 1938 convention are two events). Your own confirmed forms always count.
            if m.get("normalized") and (m.get("norm_by") == "you" or (
                    etype in ("locations", "organizations", "persons")
                    and set(re.findall(r"\d+", m["name"])) == set(re.findall(r"\d+", m["normalized"])))):
                llm_norm[m["name"]] = m["normalized"]
        counts = Counter({n: len(d) for n, d in docs.items()})
        names = sorted(docs)
        assigned: dict[str, tuple[str, str, str, str]] = {}   # name -> (canonical, status, rule, note)

        if etype == "persons":
            groups = defaultdict(list)
            for n in names:
                fam, giv = parse_person(n)
                groups[fam].append((n, giv))
            for fam, members in groups.items():
                clusters: list[list[tuple[str, list[str]]]] = []
                bare = [m for m in members if not m[1]]
                for n, giv in sorted((m for m in members if m[1]), key=lambda m: (-sum(len(g) for g in m[1]), m[0])):
                    fits = [c for c in clusters if all(given_compatible(giv, g2) for _, g2 in c)]
                    if len(fits) == 1:
                        fits[0].append((n, giv))
                    elif not fits:
                        clusters.append([(n, giv)])
                    else:
                        opts = " / ".join(best_form([x for x, _ in c], counts, llm_norm, True) for c in fits)
                        assigned[n] = (n, "suggested", "ambiguous initials", f"could be: {opts}")
                for c in clusters:
                    canon = best_form([x for x, _ in c], counts, llm_norm, True)
                    for n, _ in c:
                        assigned[n] = (canon, "auto", "same family name, compatible given names" if len(c) > 1 else "single form", "")
                for n, _ in bare:
                    if len(clusters) == 1:
                        canon = best_form([x for x, _ in clusters[0]], counts, llm_norm, True)
                        assigned[n] = (canon, "suggested", "family name only", "only one person with this family name so far")
                    else:
                        opts = " / ".join(best_form([x for x, _ in c], counts, llm_norm, True) for c in clusters)
                        assigned[n] = (n, "auto" if not clusters else "suggested", "single form" if not clusters else "family name only",
                                       f"could be: {opts}" if clusters else "")
        else:
            by_key = defaultdict(list)
            for n in names:
                by_key[other_key(etype, n)].append(n)
            groups = defaultdict(list)
            for key, vs in by_key.items():
                # a standard form known for one variant (from enrichment, or confirmed by you) carries the whole group
                norms = Counter(fold(llm_norm[v]) for v in vs if llm_norm.get(v))
                groups[norms.most_common(1)[0][0] if norms else key] += vs
            for key, vs in groups.items():
                canon = best_form(vs, counts, llm_norm)
                if etype == "organizations" and key.startswith("rotary club of ") and not any(llm_norm.get(v) for v in vs):
                    canon = "Rotary Club of " + key.split(" of ", 1)[1].title()
                for n in vs:
                    assigned[n] = (canon, "auto", "same name ignoring case, punctuation and word order" if len(vs) > 1 else "single form", "")
            # "Harbin, Manchuria" -> Harbin: plausible, left for you to confirm. The suggestion is put on the
            # group's canonical row; once confirmed, the group's other variants follow it (chains are resolved).
            canon_by_key = {other_key(etype, c): c for c, *_ in assigned.values()}
            for n in names:
                head = other_key(etype, n.split(",")[0]) if "," in n else None
                if head and head in canon_by_key and assigned[n][0] == n and canon_by_key[head] != n:
                    assigned[n] = (canon_by_key[head], "suggested", "first part of the name matches", "")

        # likely OCR / spelling variants of an established form: suggested only
        canon_forms = sorted({c for c, st, *_ in assigned.values() if st == "auto"})
        folded = {c: fold(c) for c in canon_forms}
        for n in names:
            c, st, rule, _ = assigned[n]
            if st != "auto" or rule != "single form" or len(fold(n)) < 6:
                continue
            close = [(difflib.SequenceMatcher(None, fold(n), folded[o]).ratio(), o) for o in canon_forms if o != c]
            close = [x for x in close if x[0] >= 0.88]
            if close:
                ratio, other = max(close)
                # the rarer form is proposed as a variant of the more frequent one
                if (counts[n], n) < (sum(counts[v] for v in names if assigned[v][0] == other), other):
                    assigned[n] = (other, "suggested", "similar spelling", f"similarity {ratio:.2f}")

        for n in names:
            canon, status, rule, note = assigned[n]
            rows.append({"type": etype, "name": n, "canonical": canon, "status": status, "n_docs": counts[n],
                         "clubs": "; ".join(sorted(c for c in clubs[n] if c)), "rule": rule, "note": note})
    return rows


def table_path(cfg: Config):
    return cfg.p("corrections_dir") / "entities.csv"


def read_table(cfg: Config) -> list[dict]:
    """Read the authority table as saved by any spreadsheet program: comma, semicolon (Excel with
    French settings) or tab separated; UTF-8 or, failing that, Windows / Mac encodings."""
    p = table_path(cfg)
    if not p.exists():
        return []
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "mac_roman"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if enc != "utf-8-sig":
        print(f"normalize: {p.name} is not saved as UTF-8 (read as {enc}); accented or Chinese characters may be damaged. "
              f"In Excel use 'CSV UTF-8' when saving.")
    lines = text.splitlines()
    if not lines:
        return []
    delim = max(",;\t", key=lines[0].count)
    rows, problems = [], []
    for i, r in enumerate(csv.DictReader(lines, delimiter=delim), start=2):
        extra = r.pop(None, None)   # more cells than columns on this line
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        row["status"] = row.get("status", "").lower()
        if extra or not row.get("type") or not row.get("name"):
            problems.append(i)
            continue
        if row["status"] not in ("auto", "suggested", "confirmed", "rejected"):
            problems.append(i)
            continue
        rows.append(row)
    missing = [c for c in ("type", "name", "canonical", "status") if rows and c not in rows[0]]
    if missing:
        raise SystemExit(f"{p}: column(s) {missing} not found. The first line must be: {','.join(FIELDS)}")
    if problems:
        print(f"normalize: {p.name}: {len(problems)} line(s) ignored (extra cells, empty type/name, or a status other than "
              f"auto/suggested/confirmed/rejected): lines {problems[:10]}{' ...' if len(problems) > 10 else ''}")
    return rows


def canonical_map(cfg: Config) -> dict[tuple[str, str], str]:
    """(type, name as written) -> canonical form, for the rows that are applied."""
    m = {(r["type"], r["name"]): r["canonical"] for r in read_table(cfg)
         if r.get("status") in ("auto", "confirmed") and r.get("canonical")}
    for key in list(m):  # follow chains: A -> B and B -> C gives A -> C
        seen = {key[1]}
        while (key[0], m[key]) in m and m[(key[0], m[key])] not in seen:
            seen.add(m[key])
            m[key] = m[(key[0], m[key])]
    return m


def run(cfg: Config) -> None:
    mentions = []
    for f in load_files(cfg, None, need_pdf=False):
        for row in read_jsonl(cfg.work / "enriched" / f"{f['file_number']}.jsonl"):
            e = row["enrichment"]
            for etype, ents in e["entities"].items():
                mentions += [{"type": etype, "name": x["name"], "normalized": x.get("normalized"),
                              "doc_id": row["doc_id"], "club": f["club"]} for x in ents if x.get("name")]
            for a in e.get("creators", []) + e.get("recipients", []):  # so that authors and addressees are covered too
                t = "organizations" if a.get("kind") == "organization" else "persons"
                mentions.append({"type": t, "name": a["name"], "normalized": None, "doc_id": row["doc_id"], "club": f["club"]})
    if not mentions:
        print("normalize: no entities yet (run `enrich` first)")
        return
    # your decisions take part in the grouping: a confirmed canonical form acts as the standard form of its variant
    decided = {(r["type"], r["name"]): r for r in read_table(cfg) if r.get("status") in ("confirmed", "rejected")}
    for m in mentions:
        d = decided.get((m["type"], m["name"]))
        if d and d["status"] == "confirmed":
            m["normalized"], m["norm_by"] = d["canonical"], "you"
    rows = propose(mentions)
    for r in rows:
        d = decided.get((r["type"], r["name"]))
        if d:
            r.update(canonical=d["canonical"] if d["status"] == "confirmed" else r["name"], status=d["status"],
                     rule="your decision", note=d.get("note", ""))
    seen = {(r["type"], r["name"]) for r in rows}
    rows += [{**{k: d.get(k, "") for k in FIELDS}, "n_docs": 0} for key, d in decided.items() if key not in seen]
    order = {"suggested": 0, "auto": 1, "confirmed": 2, "rejected": 3}
    rows.sort(key=lambda r: (r["type"], fold(r["canonical"]), order.get(r["status"], 9), fold(r["name"])))
    p = table_path(cfg)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8-sig", newline="") as f:  # BOM so that Excel opens it as UTF-8
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    by = Counter(r["status"] for r in rows)
    merged = sum(1 for r in rows if r["status"] in ("auto", "confirmed") and r["canonical"] != r["name"])
    n_canon = len({(r["type"], r["canonical"]) for r in rows if r["status"] in ("auto", "confirmed")})
    print(f"normalize: {len(rows)} name variants -> {n_canon} canonical entities; {merged} variants merged automatically, "
          f"{by['suggested']} suggestions await your decision, {by['confirmed'] + by['rejected']} decided by you -> {p}")
    log_run(cfg, "normalize", variants=len(rows), canonical=n_canon, statuses=dict(by))
