"""RCAD viewer. Run from the project folder:  streamlit run app/actors.py

Persons, clubs, institutions, places and events of the actor database, with statistics, a network
view and a map. The sidebar filters (name, club, classification, years) apply to every tab.
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from rctb.normalize import fold  # noqa: E402
from rctb.util import Config  # noqa: E402

st.set_page_config(page_title="Rotary in China Actor Database", layout="wide")
cfg = Config(ROOT / os.environ.get("RCTB_CONFIG", "config.yaml"))
DB = ROOT / "actors" / "out" / "rcad.sqlite"
if not DB.exists():
    st.error(f"{DB} not found. Run `python -m rcad all` first.")
    st.stop()

# Colours: a validated categorical order (blue, orange, aqua, yellow), light and dark steps; text never takes these colours.
SERIES = {"light": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"], "dark": ["#3987e5", "#d95926", "#199e70", "#c98500"]}
BLUE = "#2a78d6"
TYPES = ["person", "institution", "committee", "club"]   # fixed order: a type keeps its colour whatever the filters


@st.cache_data
def table(name: str, mtime: float) -> pd.DataFrame:
    con = sqlite3.connect(DB)
    df = pd.read_sql(f"SELECT * FROM {name}", con)
    con.close()
    return df


def T(name: str) -> pd.DataFrame:
    return table(name, DB.stat().st_mtime)


def year(v):
    return int(str(v)[:4]) if v is not None and str(v)[:4].isdigit() else None


def val(v, default=""):
    return default if v is None or (isinstance(v, float) and v != v) else v


person, club, inst, ma, oa = T("person"), T("club"), T("institution"), T("membership_attestation"), T("office_attestation")
names, stat, place, source, document = T("person_name"), T("club_stat"), T("place"), T("source"), T("document")
classif, position, committee, membership = T("classification"), T("position"), T("committee"), T("membership")
club_name = dict(zip(club["club_id"], club["name"]))
att = pd.concat([ma[["person_id", "club_id", "date_start", "date_end"]], oa[["person_id", "club_id", "date_start", "date_end"]]])
att["y0"] = att["date_start"].map(year)
att["y1"] = att["date_end"].map(year).fillna(att["y0"])
years = att["y0"].dropna()
Y0, Y1 = (int(years.min()), int(att["y1"].max())) if len(years) else (1919, 1952)

# ------------------------------------------------------------------ sidebar: filters shared by all tabs
with st.sidebar:
    st.title("RCAD")
    st.caption(f"{len(person)} persons · {int(club['has_actor_data'].sum())} of {len(club)} clubs with data · {len(inst)} institutions")
    q = st.text_input("Search a name", placeholder="Gundsi · 軍司 · Mitsui")
    data_clubs = sorted(club.loc[club["has_actor_data"] == 1, "club_id"])
    f_clubs = st.multiselect("Club", data_clubs, format_func=club_name.get)
    use_major = classif["major"].notna().any()
    class_of = dict(zip(classif["classification"], classif["major"])) if use_major else {}
    ma["class_group"] = ma["classification"].map(lambda c: val(class_of.get(c), c) if use_major else c)
    f_class = st.multiselect("Classification" + (" (major)" if use_major else " (as written)"), sorted(ma["class_group"].dropna().unique()))
    f_years = st.slider("Years", Y0, Y1, (Y0, Y1)) if Y0 < Y1 else (Y0, Y1)
    st.caption("Year filter: persons attested in at least one source dated within the range.")

# persons kept by the filters
keep = set(person["person_id"])
if q:
    fq = fold(q)
    hit = names[names.apply(lambda r: fq in fold(f"{r['surname']} {val(r['given'])}") or q in str(val(r["vernacular_name"])), axis=1)]["person_id"]
    via_inst = ma[ma["institution_as_written"].map(lambda x: fq in fold(str(val(x))))]["person_id"]
    keep &= set(hit) | set(via_inst)
if f_clubs:
    keep &= set(att[att["club_id"].isin(f_clubs)]["person_id"])
if f_class:
    keep &= set(ma[ma["class_group"].isin(f_class)]["person_id"])
if f_years != (Y0, Y1):
    keep &= set(att[(att["y0"] <= f_years[1]) & (att["y1"] >= f_years[0])]["person_id"])
P = person[person["person_id"].isin(keep)].sort_values("family_name")
in_years = lambda df: df[(df["date_start"].map(year).fillna(0) <= f_years[1]) & (df["date_end"].map(year).fillna(df["date_start"].map(year)).fillna(9999) >= f_years[0])]

HAS_WEB = bool(sqlite3.connect(DB).execute("SELECT 1 FROM sqlite_master WHERE name = 'web_statement'").fetchone())
web = T("web_statement").merge(T("web_page")[["source_id", "title", "url", "pdf_url"]], on="source_id", how="left") if HAS_WEB else pd.DataFrame()
WEB_COLS = {"subject": "About", "subject_vernacular": "中文", "attribute": "What", "value": "Value", "value_vernacular": "中文 ", "detail": "Detail",
            "date_from": "From", "date_to": "To", "place": "Place", "quote": st.column_config.TextColumn("Supporting passage", width="large"),
            "title": "Article", "pdf_page": "PDF page", "url": st.column_config.LinkColumn("Page", display_text="open")}


def web_table(df, cols=("attribute", "value", "detail", "date_from", "date_to", "place", "quote", "title", "pdf_page", "url"), **kw):
    st.dataframe(df[list(cols)], hide_index=True, width="stretch", column_config=WEB_COLS, **kw)


tabs = st.tabs(["Persons", "Clubs", "Institutions", "Places", "Events", "Statistics", "Network", "Web sources"])

# ------------------------------------------------------------------ persons
with tabs[0]:
    st.caption(f"{len(P)} of {len(person)} persons match the filters. Click a row to open the person.")
    show = P[["display_name", "vernacular_name", "clubs", "first_attested", "last_attested", "n_attestations", "n_documents", "person_id"]]
    ev = st.dataframe(show, hide_index=True, width="stretch", height=280, on_select="rerun", selection_mode="single-row",
                      column_config={"display_name": "Name", "vernacular_name": "中文 / 日本語", "clubs": "Club(s)", "first_attested": "First attested",
                                     "last_attested": "Last attested", "n_attestations": "Statements", "n_documents": "Documents", "person_id": "ID"})
    sel = ev.selection.rows if ev and ev.selection else []
    if len(P):
        p = P.iloc[sel[0]] if sel else P.iloc[0]
        pid = p["person_id"]
        st.subheader(f"{p['display_name']}  {val(p['vernacular_name'])}")
        variants = names[names["person_id"] == pid]
        st.caption(f"{pid} · also written: " + "; ".join(f"{r['surname']}, {val(r['given'], '?')}" for r in variants.to_dict("records"))
                   + (f" · nickname: {p['nicknames']}" if val(p["nicknames"]) else ""))
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**As a member** (one line per source)")
            st.dataframe(ma[ma["person_id"] == pid].sort_values("date_start")[
                ["date_start", "classification", "institution_as_written", "position_as_written", "business_address", "election_date", "sources"]],
                hide_index=True, width="stretch",
                column_config={"date_start": "Date", "classification": "Classification", "sources": "Archive pages", "institution_as_written": "Institution", "position_as_written": "Position",
                               "business_address": "Business address", "election_date": "Elected"})
        with c2:
            st.markdown("**Offices and committees**")
            o = oa[oa["person_id"] == pid].merge(position, on="position_id", how="left").merge(committee, on="committee_id", how="left")
            st.dataframe(o.sort_values("date_start")[["date_start", "date_end", "label", "name", "sources"]], hide_index=True, width="stretch",
                         column_config={"date_start": "From", "date_end": "To", "label": "Position", "name": "Committee"})
        if len(web) and (web["person_id"] == pid).any():
            st.markdown("**From rotaryinchina.org** (secondary source: a modern account, not an archive)")
            web_table(web[web["person_id"] == pid])
        pd_links = T("person_document")
        docs = pd_links[pd_links["person_id"] == pid].merge(document, on="doc_id")
        cited = sorted({x for v in pd.concat([ma.loc[ma["person_id"] == pid, "sources"], oa.loc[oa["person_id"] == pid, "sources"]]).dropna()
                        for x in v.split(", ") if x})
        c3, c4 = st.columns(2)
        with c3:
            st.markdown(f"**Named in {len(docs)} document(s) of the text database**")
            st.dataframe(docs[["doc_id", "date_produced", "genre", "title", "name_in_document"]], hide_index=True, width="stretch",
                         column_config={"doc_id": "Document", "date_produced": "Date", "genre": "Genre", "title": "Title", "name_in_document": "Named as"})
        with c4:
            st.markdown(f"**Archive pages cited ({len(cited)})**")
            pick = st.selectbox("Show page", cited, label_visibility="collapsed") if cited else None
            if pick:
                s = source[source["source_id"] == pick].iloc[0]
                img = cfg.out / "images" / s["file_number"] / f"p{int(s['page']):04d}.jpg"
                st.caption(s["citation"] + (f" · document {s['doc_id']}" if val(s["doc_id"]) else " · page not yet in the text database"))
                if img.exists():
                    st.image(str(img), width="stretch")

# ------------------------------------------------------------------ clubs
with tabs[1]:
    C = club[club["club_id"].isin(f_clubs)] if f_clubs else club
    n_p = att.groupby("club_id")["person_id"].nunique()
    C = C.assign(persons=C["club_id"].map(n_p).fillna(0).astype(int),
                 city=C.apply(lambda r: f"{val(r['city_today'])} {val(r['city_today_zh'])}".strip(), axis=1),
                 province=C.apply(lambda r: f"{val(r['province_today'])} {val(r['province_today_zh'])}".strip(), axis=1),
                 page_note=C.apply(lambda r: "" if r["folders_with_page_count"] == r["n_folders"] else
                                   f"{int(r['folders_with_page_count'])} of {int(r['n_folders'])} folders counted", axis=1)
                 ).sort_values(["has_actor_data", "name"], ascending=[False, True])
    st.caption("One line per club. Where a value changed over time (names, admissions, terminations, meeting day, language), all values "
               "are listed. Click a row for every statement with its period and source.")
    ev = st.dataframe(C[["name", "other_names", "charter_number", "country", "city", "province", "organized", "chartered", "admitted", "terminated",
                         "meeting_day", "meeting_time", "meeting_place", "languages", "publication", "publication_title", "publication_periodicity",
                         "persons", "archive_folders", "pages", "page_note"]],
                      hide_index=True, width="stretch", height=300, on_select="rerun", selection_mode="single-row",
                      column_config={"name": st.column_config.TextColumn("Name", pinned=True), "other_names": "Other names", "charter_number": "Charter no.",
                                     "country": "Country (archive)", "city": "City today", "province": "Province today", "organized": "Organized",
                                     "chartered": "Chartered", "admitted": "Admitted to RI", "terminated": "Terminated", "meeting_day": "Meeting day",
                                     "meeting_time": "Meeting time", "meeting_place": "Meeting place", "languages": "Language(s)",
                                     "publication": "Publication", "publication_title": "Publication title", "publication_periodicity": "Periodicity",
                                     "persons": "Persons in database", "archive_folders": "Archive folders",
                                     "pages": st.column_config.ProgressColumn("Pages", format="%d", min_value=0, max_value=int(val(C["pages"].max(), 1) or 1)),
                                     "page_note": "Page count covers"})
    sel = ev.selection.rows if ev and ev.selection else []
    statements = T("club_statement")
    shown = [C.iloc[sel[0]]["club_id"]] if sel else (f_clubs or data_clubs)[:3]
    for cid in shown:
        st.subheader(club_name[cid])
        s_ = statements[statements["club_id"] == cid]
        with st.expander(f"Identification: {len(s_)} statements with their sources", expanded=bool(sel)):
            st.dataframe(s_[["attribute", "value", "detail", "date_from", "date_to", "source", "status"]], hide_index=True, width="stretch",
                         column_config={"attribute": "What", "value": "Value", "detail": "Detail", "date_from": "From", "date_to": "To",
                                        "source": "Source", "status": "Status"})
            st.caption("`auto`: read from the archive index or the header of your notes. To add or correct: actors/authority/club_statements.csv.")
        if len(web) and (web["club_id"] == cid).any():
            with st.expander(f"From rotaryinchina.org: {int((web['club_id'] == cid).sum())} statements (secondary source)"):
                web_table(web[web["club_id"] == cid])
        if not club.set_index("club_id").loc[cid, "has_actor_data"]:
            st.caption("No members, officers or notes entered for this club yet.")
            continue
        off = oa[(oa["club_id"] == cid) & oa["committee_id"].isna()].merge(position, on="position_id", how="left").merge(
            person[["person_id", "display_name"]], on="person_id", how="left")
        off = in_years(off)
        st.markdown("**Officers and directors**")
        st.dataframe(off.sort_values(["date_start", "label"])[["date_start", "date_end", "label", "display_name", "sources"]], hide_index=True,
                     width="stretch", height=240, column_config={"date_start": "From", "date_end": "To", "label": "Position", "display_name": "Person"})
        notes = T("note")
        n = notes[notes["club_id"] == cid]
        if q:
            n = n[n["text"].str.contains(q, case=False, regex=False)]
        with st.expander(f"Research notes ({len(n)})" + (f" containing “{q}”" if q else "")):
            st.dataframe(n[["file_number", "pages_as_written", "text"]], hide_index=True, width="stretch",
                         column_config={"file_number": "Folder", "pages_as_written": "Pages", "text": st.column_config.TextColumn("Note", width="large")})

# ------------------------------------------------------------------ institutions
with tabs[2]:
    m = ma[ma["person_id"].isin(keep) & ma["institution_id"].notna()]
    m = in_years(m)
    agg = m.groupby("institution_id").agg(persons=("person_id", "nunique"), positions=("position_as_written", lambda x: "; ".join(sorted(set(x.dropna()))[:4])),
                                          classifications=("classification", lambda x: "; ".join(sorted(set(x.dropna()))[:3])),
                                          addresses=("business_address", lambda x: "; ".join(sorted(set(x.dropna()))[:2]))).reset_index()
    I = inst.merge(agg, on="institution_id").sort_values("persons", ascending=False)
    st.caption(f"{len(I)} institutions linked to the persons matching the filters. Click a row to list its people.")
    ev = st.dataframe(I[["name", "vernacular_name", "persons", "classifications", "positions", "addresses", "institution_id"]], hide_index=True,
                      width="stretch", height=320, on_select="rerun", selection_mode="single-row",
                      column_config={"name": "Institution", "vernacular_name": "中文 / 日本語", "persons": "Persons", "institution_id": "ID"})
    sel = ev.selection.rows if ev and ev.selection else []
    if sel:
        iid = I.iloc[sel[0]]["institution_id"]
        st.markdown(f"**People at {I.iloc[sel[0]]['name']}**")
        st.dataframe(m[m["institution_id"] == iid].merge(person[["person_id", "display_name"]], on="person_id")[
            ["display_name", "position_as_written", "date_start", "classification", "sources"]], hide_index=True, width="stretch")

# ------------------------------------------------------------------ places
with tabs[3]:
    per = T("club_period")
    per["y0"], per["y1"] = per["start"].map(year), per["end"].map(year)
    cs = T("club_statement")
    cs = cs[cs["attribute"].isin(["organized", "admitted", "terminated"])].assign(y=lambda d: d["value"].map(year))
    all_y = pd.concat([per["y0"], per["y1"], cs["y"]]).dropna()
    PY0, PY1 = (int(all_y.min()), int(all_y.max())) if len(all_y) else (1919, 1952)
    c1, c2 = st.columns([1, 1])
    what = c1.radio("Show the clubs that were", ["in existence", "organized", "admitted to RI", "terminated"], horizontal=True,
                    help="In existence: between an admission and the following termination. A club terminated during the war and "
                         "readmitted afterwards disappears from the map for the years in between.")
    p_years = c2.slider("during the years", PY0, PY1, (PY0, PY1), key="place_years") if PY0 < PY1 else (PY0, PY1)
    if what == "in existence":
        on = per[(per["y0"].fillna(PY0) <= p_years[1]) & (per["y1"].fillna(PY1) >= p_years[0])]
        dates = on.assign(d=on["start"].fillna("?") + " to " + on["end"].fillna("?")).groupby("club_id")["d"].agg("; ".join)
    else:
        on = cs[(cs["attribute"] == what.split()[0]) & (cs["y"] >= p_years[0]) & (cs["y"] <= p_years[1])]
        dates = on.groupby("club_id")["value"].agg(lambda x: "; ".join(sorted(x)))
    CP = club[club["club_id"].isin(dates.index)].assign(dates=lambda d: d["club_id"].map(dates), persons=lambda d: d["club_id"].map(n_p).fillna(0).astype(int))
    pl = place.dropna(subset=["latitude", "longitude"]).merge(
        CP.groupby("place_id").agg(clubs=("name", lambda x: ", ".join(x)), has_data=("has_actor_data", "max"), persons=("persons", "sum")).reset_index(), on="place_id")
    pl["color"] = pl["has_data"].map({1: BLUE, 0: "#898781"})
    pl["size"] = 12000 + pl["persons"] * 900
    st.caption(f"{len(CP)} of {len(club)} clubs, in {len(pl)} cities. Blue: clubs with data in this database; grey: clubs of the archive not yet "
               "entered. Coordinates are approximate city centres from actors/authority/places.csv.")
    if len(pl):
        st.map(pl, latitude="latitude", longitude="longitude", color="color", size="size", height=520)
    else:
        st.info("No club for this selection.")
    CP = CP.merge(place[["place_id", "modern_name", "modern_name_zh", "province", "province_zh"]], on="place_id", how="left")
    st.dataframe(CP.sort_values("dates")[["name", "dates", "modern_name", "modern_name_zh", "province", "province_zh", "persons"]], hide_index=True, width="stretch",
                 column_config={"name": "Club", "dates": "In existence" if what == "in existence" else what.capitalize(), "modern_name": "City today",
                                "modern_name_zh": "城市", "province": "Province today", "province_zh": "省", "persons": "Persons in database"})
    st.caption("Members' business and private addresses are recorded as text and are not yet placed on the map.")

# ------------------------------------------------------------------ events
with tabs[4]:
    st.caption("The actor database has no reviewed table of events yet. Below: events named in documents of the text database, "
               "as extracted by the model, unreviewed.")
    rctb = cfg.out / "rctb.sqlite"
    if rctb.exists():
        con = sqlite3.connect(rctb)
        cols = [r[1] for r in con.execute("PRAGMA table_info(entities)")]
        name_col = "canonical" if "canonical" in cols else "name"
        e = pd.read_sql(f"SELECT e.{name_col} AS event, d.club, count(DISTINCT e.doc_id) AS documents, min(d.date_produced) AS first_document, "
                        f"group_concat(DISTINCT e.doc_id) AS doc_ids FROM entities e JOIN documents d USING (doc_id) WHERE e.type = 'events' "
                        f"GROUP BY 1, 2 ORDER BY documents DESC", con)
        con.close()
        if q:
            e = e[e["event"].str.contains(q, case=False, regex=False)]
        st.dataframe(e, hide_index=True, width="stretch")
    else:
        st.info("Text database not found.")

# ------------------------------------------------------------------ statistics
def chart(c):
    return c.configure_axis(gridColor="#e1e0d9", domainColor="#c3c2b7", tickColor="#c3c2b7", labelColor="#52514e", titleColor="#52514e") \
            .configure_view(strokeWidth=0).configure_legend(labelColor="#52514e", titleColor="#52514e")


with tabs[5]:
    S = stat[stat["club_id"].isin(f_clubs or data_clubs)].copy()
    S["month"] = pd.to_datetime(S["date"].map(lambda d: d if len(str(d)) > 4 else f"{d}-01"), errors="coerce")
    S = S[(S["month"].dt.year >= f_years[0]) & (S["month"].dt.year <= f_years[1])]
    S["club"] = S["club_id"].map(club_name)
    many = S["club"].nunique() > 1
    k1, k2, k3 = st.columns(3)
    k1.metric("Persons matching the filters", len(P))
    k2.metric("Institutions", ma[ma["person_id"].isin(keep)]["institution_id"].nunique())
    k3.metric("Charter members", int(membership[membership["person_id"].isin(keep)]["charter_member"].sum()))

    def time_axis(d):
        span = (d["month"].max() - d["month"].min()).days
        return alt.Axis(format="%b %Y" if span < 900 else "%Y", labelOverlap=True, tickCount=8)

    def line(measure, title, ytitle):
        d = S[S["measure"] == measure]
        if d.empty:
            return st.caption(f"{title}: no figures for this selection.")
        # sources sometimes give different figures for the same month: the line follows their median, the tooltip shows the range
        d = d.groupby(["club", "month"]).agg(value=("value", "median"), low=("value", "min"), high=("value", "max"), n=("value", "size"),
                                             sources=("sources", lambda x: ", ".join(sorted(set(x.dropna()))))).reset_index().sort_values("month")
        d["figures"] = d.apply(lambda r: f"{r['low']:g}" if r["low"] == r["high"] else f"{r['low']:g} to {r['high']:g} ({r['n']} sources)", axis=1)
        enc = dict(x=alt.X("month:T", title=None, axis=time_axis(d)), y=alt.Y("value:Q", title=ytitle, scale=alt.Scale(zero=measure != "attendance_percent")),
                   tooltip=[alt.Tooltip("club:N"), alt.Tooltip("month:T", format="%B %Y"), alt.Tooltip("figures:N", title=ytitle), alt.Tooltip("sources:N", title="source")])
        if many:
            enc["color"] = alt.Color("club:N", title=None, scale=alt.Scale(range=SERIES["light"]))
        base = alt.Chart(d, title=title).encode(**enc)
        st.altair_chart(chart(base.mark_line(strokeWidth=2, color=BLUE) + base.mark_point(size=40, filled=True, color=BLUE, stroke="#fcfcfb", strokeWidth=1.5))
                        .properties(height=260), width="stretch")

    c1, c2 = st.columns(2)
    with c1:
        line("members", "Membership over time", "Members")
    with c2:
        line("attendance_percent", "Average attendance at meetings", "Attendance (%)")
    d = S[S["measure"].isin(["accessions", "terminations"])]
    if not d.empty:
        st.altair_chart(chart(alt.Chart(d, title="Members joining and leaving, per month").mark_bar(size=7, cornerRadiusTopLeft=3, cornerRadiusTopRight=3).encode(
            x=alt.X("yearmonth(month):T", title=None, axis=time_axis(d)), xOffset="measure:N", y=alt.Y("value:Q", title="Members"),
            color=alt.Color("measure:N", title=None, scale=alt.Scale(domain=["accessions", "terminations"], range=SERIES["light"][:2])),
            tooltip=[alt.Tooltip("month:T", format="%B %Y"), "measure:N", alt.Tooltip("value:Q", title="members"), alt.Tooltip("sources:N", title="source")])
            .properties(height=240)), width="stretch")

    c3, c4 = st.columns(2)
    with c3:
        cl = in_years(ma[ma["person_id"].isin(keep)]).groupby("class_group")["person_id"].nunique().sort_values(ascending=False)
        top = cl.head(15).reset_index()
        top.columns = ["classification", "persons"]
        bars = alt.Chart(top, title=f"Classifications ({'major' if use_major else 'as written'}): top {len(top)} of {len(cl)}").encode(
            y=alt.Y("classification:N", sort="-x", title=None, axis=alt.Axis(labelLimit=240, labelOverlap=False)), x=alt.X("persons:Q", title="Persons", axis=alt.Axis(tickMinStep=1)),
            tooltip=["classification:N", "persons:Q"])
        st.altair_chart(chart((bars.mark_bar(size=14, color=BLUE, cornerRadiusTopRight=4, cornerRadiusBottomRight=4)
                               + bars.mark_text(align="left", dx=4, color="#52514e").encode(text="persons:Q")).properties(height=alt.Step(24))), width="stretch")
        if not use_major:
            st.caption("Wordings are counted as written in the sources. Fill `major` in actors/authority/classifications.csv to group them.")
    with c4:
        a = att[att["person_id"].isin(keep)].dropna(subset=["y0"])
        per_year = a.groupby(a["y0"].astype(int))["person_id"].nunique().reset_index()
        per_year.columns = ["year", "persons"]
        per_year = per_year[(per_year["year"] >= f_years[0]) & (per_year["year"] <= f_years[1])]
        b = alt.Chart(per_year, title="Persons named in sources, by year of the source").encode(
            x=alt.X("year:O", title=None), y=alt.Y("persons:Q", title="Persons"), tooltip=["year:O", "persons:Q"])
        st.altair_chart(chart((b.mark_bar(size=20, color=BLUE, cornerRadiusTopLeft=4, cornerRadiusTopRight=4)
                               + b.mark_text(dy=-6, color="#52514e").encode(text="persons:Q")).properties(height=300)), width="stretch")
        st.caption("This counts who appears in the sources entered so far for each year, not the club's membership: "
                   "years with a full roster stand out.")
    with st.expander("Figures behind the charts"):
        st.dataframe(S[["club", "date", "measure", "value", "sources"]], hide_index=True, width="stretch")

# ------------------------------------------------------------------ network
with tabs[6]:
    nodes = pd.read_csv(DB.parent / "graph" / "nodes.csv")
    c1, c2, c3 = st.columns([2, 2, 3])
    mode = c1.radio("Show", ["Persons linked to each other", "Persons and what links them"],
                    help="First: two persons are linked when they sat on the same committee or worked for the same institution. "
                         "Second: persons, institutions and committees as separate points.")
    rels = c2.multiselect("Links through", ["institution", "committee"], default=["institution", "committee"])
    focus = c3.selectbox("Centre on a person (optional)", [None] + list(P["person_id"]), format_func=lambda i: "(everyone matching the filters)" if i is None
                         else P.set_index("person_id").loc[i, "display_name"])
    c4, c5 = st.columns([3, 2])
    n_years = c4.slider("Years of service (on the committee, or attested at the institution)", Y0, Y1, f_years, key="net_years") if Y0 < Y1 else (Y0, Y1)
    same_time = c5.checkbox("Link two persons only if they served in the same year(s)", value=True,
                            help="Unticked: two persons are linked when they sat on the same committee or worked for the same institution "
                                 "at any time within the range, even years apart.") if mode.startswith("Persons linked") else False
    ntype = dict(zip(nodes["id"], nodes["type"]))
    label = dict(zip(nodes["id"], nodes["label"]))
    # one line per statement: who, linked to what, from which year to which year (a statement with one date counts for that year)
    L = pd.concat([oa[oa["committee_id"].notna()].merge(position, on="position_id", how="left").rename(columns={"committee_id": "target", "label": "role"}),
                   ma[ma["institution_id"].notna()].rename(columns={"institution_id": "target", "position_as_written": "role"})])[
        ["person_id", "target", "role", "date_start", "date_end"]].rename(columns={"person_id": "source"})
    L["y0"] = L["date_start"].map(year)
    L["y1"] = L["date_end"].map(year).fillna(L["y0"])
    undated = int(L["y0"].isna().sum())
    E = L[L["source"].isin(keep) & L["target"].map(ntype).isin(rels) & (L["y0"] <= n_years[1]) & (L["y1"] >= n_years[0])].copy()
    E["y0"], E["y1"] = E["y0"].clip(lower=n_years[0]).astype(int), E["y1"].clip(upper=n_years[1]).astype(int)
    span = lambda a, b: str(a) if a == b else f"{a}–{b}"
    if mode.startswith("Persons linked"):
        pp = E.merge(E, on="target")
        pp = pp[pp["source_x"] < pp["source_y"]]
        if same_time:
            pp = pp[(pp["y0_x"] <= pp["y1_y"]) & (pp["y0_y"] <= pp["y1_x"])]
        pp = pp.assign(a=pp[["y0_x", "y0_y"]].max(axis=1) if same_time else pp[["y0_x", "y0_y"]].min(axis=1),
                       b=pp[["y1_x", "y1_y"]].min(axis=1) if same_time else pp[["y1_x", "y1_y"]].max(axis=1))
        G = pp.groupby(["source_x", "source_y"]).agg(weight=("target", "nunique"), via=("target", lambda x: "; ".join(sorted(label[t] for t in set(x))[:4])),
                                                     a=("a", "min"), b=("b", "max")).reset_index()
        G["years"] = [span(a, b) for a, b in zip(G["a"], G["b"])]
        G = G.drop(columns=["a", "b"])
        G.columns = ["source", "target", "weight", "via", "years"]
    else:
        G = E.groupby(["source", "target"]).agg(weight=("role", "size"), via=("role", lambda x: "; ".join(sorted(set(map(str, x.dropna())))[:3])),
                                                a=("y0", "min"), b=("y1", "max")).reset_index()
        G["years"] = [span(a, b) for a, b in zip(G["a"], G["b"])]
        G = G.drop(columns=["a", "b"])
    if len(G):
        G["via"] = G["via"] + " (" + G["years"] + ")"
    if mode.startswith("Persons linked") and len(G) and G["weight"].max() > 1:
        strength = st.slider("Minimum strength of a link (number of committees or institutions two persons shared)", 1, int(G["weight"].max()),
                             2 if len(G) > 250 and not focus else 1)
        G = G[G["weight"] >= strength]
    if focus:
        near = {focus} | set(G.loc[G["source"] == focus, "target"]) | set(G.loc[G["target"] == focus, "source"])
        G = G[G["source"].isin(near) & G["target"].isin(near)]
    ids = sorted(set(G["source"]) | set(G["target"]))
    if not ids:
        st.info("No links for this selection.")
    elif len(ids) > 700:
        st.warning(f"{len(ids)} points is too many to draw legibly. Narrow the filters or centre on a person.")
    else:
        deg = pd.concat([G["source"], G["target"]]).value_counts()
        payload = {"nodes": [{"id": i, "label": label.get(i, i), "type": ntype.get(i, "person"), "deg": int(deg[i]), "focus": i == focus} for i in ids],
                   "links": G.to_dict("records"), "types": [t for t in TYPES if t in {ntype.get(i) for i in ids}], "colors": SERIES, "order": TYPES}
        st.caption(f"{len(ids)} points, {len(G)} links, {span(*n_years)}"
                   + (f" ({undated} undated statements left out)" if undated else "") + ". Drag a point to move it, scroll to zoom, drag the background to pan, hover for details.")
        page = (Path(__file__).parent / "network.html").read_text(encoding="utf-8").replace(
            "/*DATA*/", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/"))   # data cannot close the script block
        if hasattr(st, "iframe"):
            st.iframe(page, height=680)
        else:
            components.html(page, height=680)
        with st.expander("The links as a table"):
            st.dataframe(G.assign(source=G["source"].map(label), target=G["target"].map(label)).sort_values("weight", ascending=False),
                         hide_index=True, width="stretch", column_config={"weight": "Strength", "via": "Through", "years": "Years"})

# ------------------------------------------------------------------ web sources
with tabs[7]:
    if not len(web):
        st.info("No statements from rotaryinchina.org yet. Run `python -m rcad web`, then `python -m rcad build`.")
    else:
        st.caption("Statements read by a model in the articles of rotaryinchina.org. They are **secondary**: a modern account, kept apart from "
                   "what the archives say. Each one shows the passage it rests on, which was checked to be in the article word for word. "
                   "The name search of the sidebar applies here too.")
        c1, c2, c3 = st.columns(3)
        w_type = c1.multiselect("About a", sorted(web["subject_type"].dropna().unique()))
        w_attr = c2.multiselect("What", sorted(web["attribute"].dropna().unique()))
        w_link = c3.radio("Linked to the database", ["all", "linked", "not linked"], horizontal=True,
                          help="Linked: the person or club is one already in the database. Matches are proposed in actors/authority/web_subjects.csv.")
        W = web
        if q:
            fq = fold(q)
            hit = W.apply(lambda r: any(fq in fold(str(val(r[c]))) or q in str(val(r[c])) for c in ("subject", "subject_vernacular", "value", "value_vernacular", "detail")), axis=1)
            W = W[hit | W["subject_id"].isin(W.loc[hit, "subject_id"])]   # every statement about the subjects found, however they are spelt
        if w_type:
            W = W[W["subject_type"].isin(w_type)]
        if w_attr:
            W = W[W["attribute"].isin(w_attr)]
        if w_link != "all":
            linked = W["person_id"].notna() | W["club_id"].notna()
            W = W[linked if w_link == "linked" else ~linked]
        if f_years != (Y0, Y1):
            W = W[(W["date_from"].map(year).fillna(0) <= f_years[1]) & (W["date_to"].map(year).fillna(W["date_from"].map(year)).fillna(9999) >= f_years[0])]
        subj = T("web_subject")
        k1, k2, k3 = st.columns(3)
        k1.metric("Statements", len(W))
        k2.metric("Persons, clubs, institutions, events", W["subject_id"].nunique())
        k3.metric("Articles", W["source_id"].nunique())
        web_table(W.sort_values(["subject", "date_from"]), cols=("subject", "subject_vernacular", "attribute", "value", "value_vernacular", "detail",
                                                                  "date_from", "date_to", "place", "quote", "title", "pdf_page", "url"), height=520)
        with st.expander("Who and what the statements are about, and the matches proposed"):
            st.dataframe(subj[subj["subject_id"].isin(W["subject_id"])].sort_values("n_statements", ascending=False), hide_index=True, width="stretch",
                         column_config={"match_id": "Matched to", "status": "Match status", "n_statements": "Statements", "n_pages": "Articles"})
