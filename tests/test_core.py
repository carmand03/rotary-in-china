import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from rctb.models import DateInfo, Document, Enrichment, json_schema
from rctb.s00_inventory import iso_date
from rctb.s02_llm_stages import labels_to_docs, merge_windows
from rctb.search import chunk_document
from rctb.util import script_shares, text_quality


def L(page, rel, cont=None):
    return {"page": page, "relation": rel, "continues_page": cont}


def test_labels_to_docs_handles_blank_and_non_successive_pages():
    labels = [L(1, "new"), L(2, "continues"), L(3, "blank"), L(4, "new"), L(5, "continues_earlier", 2), L(6, "continues")]
    docs, warnings = labels_to_docs(labels)
    assert docs == [[1, 2, 5, 6], [4]]
    assert warnings == []


def test_continues_earlier_to_unknown_page_starts_new_doc_and_warns():
    docs, warnings = labels_to_docs([L(1, "new"), L(2, "continues_earlier", 9)])
    assert docs == [[1], [2]] and len(warnings) == 1


def test_merge_windows_trusts_previous_window_for_first_page_only():
    w1 = [L(1, "new"), L(2, "continues"), L(3, "new"), L(4, "continues")]
    w2 = [L(3, "new"), L(4, "new"), L(5, "continues")]  # page 3 unseen context -> ignored; page 4 overrides
    merged = {l["page"]: l["relation"] for l in merge_windows([w1, w2])}
    assert merged == {1: "new", 2: "continues", 3: "new", 4: "new", 5: "continues"}


def test_dates_must_be_iso_or_truncated_iso():
    assert DateInfo(type="produced", value="1936-07").value == "1936-07"
    # slips are repaired rather than failing the whole record; wording that is no ISO date becomes "no value"
    assert DateInfo(type="produced", value="1936-07-00").value == "1936-07"
    assert DateInfo(type="produced", value="1936-7-3").value == "1936-07-03"
    assert DateInfo(type="produced", value="3 July 1936", as_written="3 July 1936").value is None
    with pytest.raises(Exception):
        DateInfo(type="invented-role", value="1936")


def test_enrichment_tolerates_common_slips():
    e = Enrichment(genre="roster", title="Membership List", summary=None, dates=None, languages="en, ja",
                   creators=["Harbin Rotary Club"], entities={"persons": ["K. Ehara", {"name": ""}, {"name": "T. Maeda", "role": "Treasurer"}]})
    assert e.title.value == "Membership List" and e.dates == [] and e.languages == ["en", "ja"]
    assert [p.name for p in e.entities.persons] == ["K. Ehara", "T. Maeda"] and e.creators[0].name == "Harbin Rotary Club"


def test_index_dates():
    assert iso_date("1933-Oct-23") == "1933-10-23"
    assert iso_date("1948-Jun-2") == "1948-06-02"
    assert iso_date(1931) == "1931"


def test_schema_is_self_contained_and_carries_genre_vocabulary():
    s = json_schema(Enrichment, ["roster", "other"])
    assert "$ref" not in json.dumps(s)
    assert s["properties"]["genre"]["enum"] == ["roster", "other"]
    assert "full_text" in json_schema(Document)["properties"]
    # every required field must be described: a field named "title" must survive the removal of "title" annotations
    def check(node):
        if isinstance(node, dict):
            if "properties" in node:
                assert set(node.get("required", [])) <= set(node["properties"]), node.get("required")
            for v in node.values():
                check(v)
        elif isinstance(node, list):
            for v in node:
                check(v)
    from rctb.llm import strip_keys
    from rctb.models import RagAnswer, Segmentation, Transcription
    for model in (Enrichment, Document, Segmentation, Transcription, RagAnswer):
        check(json_schema(model))
        check(strip_keys(json_schema(model)))
    assert "title" in json_schema(Enrichment)["properties"]


def test_chunks_cover_text_and_know_their_pages():
    text = ("alpha " * 100).strip() + "\n\n" + ("beta " * 100).strip()
    doc = {"doc_id": "X-p001", "full_text": text, "page_offsets": json.dumps({"1": 0, "2": text.index("beta")})}
    chunks = chunk_document(doc, size=200, overlap=40)
    assert chunks[0]["char_start"] == 0 and chunks[-1]["char_end"] == len(text)
    assert json.loads(chunks[0]["pages"]) == [1] and json.loads(chunks[-1]["pages"]) == [2]
    assert all(text[c["char_start"]:c["char_end"]] == c["text"] for c in chunks)


def test_script_and_quality_indicators():
    assert script_shares("Harbin 哈爾濱")["cjk"] > 0
    spaced = text_quality("H a r b i n J u l y")
    assert spaced["single_char_ratio"] == 1.0


def test_entity_variants_are_grouped_conservatively():
    from rctb.normalize import propose

    def m(t, name, doc, norm=None):
        return {"type": t, "name": name, "normalized": norm, "doc_id": doc, "club": "Harbin"}

    rows = {(r["type"], r["name"]): r for r in propose([
        m("persons", "Y. Gundsi", "d1"), m("persons", "Yoshio GUNDSI", "d2"), m("persons", "Gundsi, Y.", "d3"),
        m("persons", "Koichi Ehara", "d1"), m("persons", "Kenji Ehara", "d2"), m("persons", "K. Ehara", "d3"),
        m("persons", "Minsky", "d1"), m("persons", "V. Minsky", "d2"),
        m("organizations", "ROTARY CLUB OF HARBIN, MANCHURIA", "d1"), m("organizations", "Harbin Rotary Club", "d2"),
        m("locations", "Tientsin", "d1", "Tianjin"), m("locations", "TIENTSIN", "d2"),
    ])}
    # initials and full names of the same person merge, whatever the order or case
    assert {rows[("persons", n)]["canonical"] for n in ("Y. Gundsi", "Yoshio GUNDSI", "Gundsi, Y.")} == {"Yoshio GUNDSI"}
    # an initial that fits two different people is not merged
    assert rows[("persons", "K. Ehara")]["status"] == "suggested" and rows[("persons", "K. Ehara")]["canonical"] == "K. Ehara"
    assert rows[("persons", "Koichi Ehara")]["canonical"] != rows[("persons", "Kenji Ehara")]["canonical"]
    # a bare family name is only a suggestion
    assert rows[("persons", "Minsky")]["status"] == "suggested" and rows[("persons", "Minsky")]["canonical"] == "V. Minsky"
    assert rows[("organizations", "Harbin Rotary Club")]["canonical"] == "Rotary Club of Harbin"
    assert rows[("organizations", "ROTARY CLUB OF HARBIN, MANCHURIA")]["status"] == "auto"
    # a standard form known for one variant carries the group
    assert rows[("locations", "TIENTSIN")]["canonical"] == "Tianjin"


def test_boundary_rules_on_a_typical_run_of_pages():
    from rctb.s02_llm_stages import decide_from_facts

    def pg(kind, heading=None, **kw):
        base = dict(is_blank=False, page_kind=kind, heading=heading, has_letterhead=False, date_line=None, has_salutation=False,
                    begins_mid_text=False, printed_page_number=None, looks_like_reverse_side=False, has_closing_signature=False)
        return {**base, **kw}

    run = [
        pg("cover_or_contents", "HISTORICAL BINDER"),                                  # 1 new
        pg("cover_or_contents", "TABLE OF CONTENTS"),                                  # 2 new: own heading
        pg("roster_or_list", "Membership List"),                                       # 3 new: other kind
        pg("roster_or_list", None, begins_mid_text=True),                              # 4 continues
        pg("roster_or_list", "New Officers and Board of Directors 1936-7"),           # 5 new: own heading
        pg("form", "Semi-Annual Report of Club Membership"),                           # 6 new
        pg("letter", "ROTARY CLUB OF HARBIN", has_letterhead=True, date_line="Harbin, July 3rd, 1936", has_salutation=True),  # 7 new
        pg("form", "Semi-Annual Report of Club Membership"),                           # 8 new
        pg("letter", "ROTARY CLUB OF HARBIN", has_letterhead=True, has_salutation=True),  # 9 new (letter after a form)
        pg("bulletin", "THE HARBIN ROTARIAN No. 49", printed_page_number=1),          # 10 new
        pg("bulletin", "THE HARBIN ROTARIAN", printed_page_number=2),                 # 11 continues
        pg("bulletin", None, begins_mid_text=True, printed_page_number=3),            # 12 continues
        dict(pg("other"), is_blank=True),                                              # 13 blank
        pg("bulletin", "THE HARBIN ROTARIAN No. 55", printed_page_number=1),          # 14 new: numbered 1 again
        pg("certificate", "CREDENTIAL CERTIFICATE"),                                   # 15 new
        pg("regulations", None, looks_like_reverse_side=True),                         # 16 continues: reverse of the certificate
        pg("record_card", "SEMI-ANNUAL REPORTS"),                                      # 17 new
        pg("record_card", None),                                                       # 18 new: another card
        pg("record_card", "FOLLOW-UP WORK"),                                           # 19 new
        pg("letter", None, begins_mid_text=True, has_closing_signature=True),         # 20 new: other kind (a stray second page)
        pg("letter", None, begins_mid_text=True),                                      # 21 continues
    ]
    got, prev = [], None
    for cur in run:
        rel, _ = decide_from_facts(cur, prev)
        got.append(rel)
        if rel != "blank":
            prev = cur
    expected = ["new", "new", "new", "continues", "new", "new", "new", "new", "new", "new", "continues", "continues",
                "blank", "new", "new", "continues", "new", "new", "new", "new", "continues"]
    assert got == expected, [(i + 1, g, e) for i, (g, e) in enumerate(zip(got, expected)) if g != e]


def test_model_normalisation_cannot_merge_different_events():
    from rctb.normalize import propose

    def m(t, name, doc, norm=None):
        return {"type": t, "name": name, "normalized": norm, "doc_id": doc, "club": "Harbin"}

    rows = {(r["type"], r["name"]): r for r in propose([
        m("events", "1937 Convention, Nice", "d1", "Rotary International Convention"),
        m("events", "1938 Convention, San Francisco", "d2", "Rotary International Convention"),
        m("locations", "Tientsin", "d1", "Tianjin"), m("locations", "Mukden", "d2", "Shenyang"),
    ])}
    assert rows[("events", "1937 Convention, Nice")]["canonical"] != rows[("events", "1938 Convention, San Francisco")]["canonical"]
    assert rows[("locations", "Tientsin")]["canonical"] == "Tianjin"


def test_schemas_sent_to_the_model_require_every_field():
    from rctb.s02_llm_stages import json_schema as llm_schema
    from rctb.models import Entities, PageFacts
    assert set(llm_schema(Entities)["required"]) == {"persons", "organizations", "locations", "events", "works"}
    assert "page_kind" in llm_schema(PageFacts)["required"]
    assert "name" in llm_schema(Entities)["properties"]["persons"]["items"]["required"]


def test_entity_text_is_read_in_pieces_and_merged():
    from rctb.s02_llm_stages import _text_chunks, merge_entities
    pages = {1: {"text": "a" * 50}, 2: {"text": ("line of names\n" * 40)}, 3: {"text": "c" * 30}}
    chunks = _text_chunks([1, 2, 3], pages, 200)
    assert len(chunks) > 3 and all(len(c) <= 200 for c in chunks)
    assert "".join(chunks).count("line of names") == 40
    merged = merge_entities([{"persons": [{"name": "Y. Gundsi", "role": "Secretary"}]},
                             {"persons": [{"name": "y.  gundsi"}, {"name": "K. Ehara"}], "works": [{"name": "The Rotarian"}]}])
    assert [p["name"] for p in merged["persons"]] == ["Y. Gundsi", "K. Ehara"] and len(merged["works"]) == 1


def test_actor_notes_and_source_references_are_parsed():
    from rcad.ingest import PAGE_LINE, PAGE_LOOSE, REF, ym
    def pages(line):
        m = PAGE_LINE.match(line) or PAGE_LOOSE.match(line)
        return (int(m.group(1)), int(m.group(2)) if m.group(2) else None, m.groups()[-1][:12]) if m else None
    assert pages("p.11-12 : Application for membership") == (11, 12, "Application ")
    assert pages("p.6: Card record") == (6, None, "Card record")
    assert pages("p. 175-176 : Letter from James") == (175, 176, "Letter from ")
    assert pages("282-285 : Correspondence between RI") == (282, 285, "Corresponden")
    assert pages("p.108-139, 150-158: Russian translations")[:2] == (108, 139)
    assert pages("p.163-165 Correspondence with E. Lenox Simpson")[:2] == (163, 165)
    assert pages("Simpson was the brother of Bertram") is None
    assert REF.findall("B36-F05-02, B37-F01-03") == [("B36-F05", "02"), ("B37-F01", "03")]
    assert ym(1936.0, 6.0) == "1936-06" and ym(1936.0) == "1936" and ym(None) is None


def test_club_statements_from_notes_header_and_periods():
    import pandas as pd
    from rcad.clubs import parse_date, parse_header_line, periods
    assert parse_date("24 April 1930") == "1930-04-24" and parse_date("April 24, 1930") == "1930-04-24" and parse_date("Apr. 1930") == "1930-04"
    got = lambda line: {(d["attribute"], d["value"]) for d in parse_header_line(line)}
    assert got("Harbin, Manchuria (No. 3334)") == {("charter_number", "3334")}
    assert got("Organized 24 April 1930") == {("organized", "1930-04-24")}
    assert got("Readmitted 14 August 1946") == {("admitted", "1946-08-14")}
    assert got("Meetings Thursday 1pm, Nichiman Club") == {("meeting_day", "Thursday"), ("meeting_time", "1pm"), ("meeting_place", "Nichiman Club")}
    assert got("Languages: English, Russian") == {("language", "English"), ("language", "Russian")}
    assert got("Charter members: 22") == set() and got("Population : 126,000 (1930)") == set()
    assert parse_header_line("Also known as: Peiping (1928-1949)")[0] == {"attribute": "name", "value": "Peiping", "date_from": "1928", "date_to": "1949"}
    assert parse_header_line("Publication: The Pagoda (weekly)")[0] == {"attribute": "publication", "value": "The Pagoda", "detail": "weekly"}
    st = pd.DataFrame([("a", "admitted", "1933-10-23"), ("a", "terminated", "1943-12-21"), ("a", "admitted", "1948-06-02"), ("a", "terminated", "1950-12-21"),
                       ("a", "chartered", "1933-10-23")], columns=["club_id", "attribute", "value"])
    assert [(p["start"], p["end"]) for p in periods(st)] == [("1933-10-23", "1943-12-21"), ("1948-06-02", "1950-12-21")]


def test_web_statements_text_and_quote_checks():
    from rcad.web import Statements, chunks, club_key, squash, summary_en
    page = "Title\nhttps://x/\n\nTitle\nENG\nThe club was admitted in April 1932.\nPTG\nO clube...\n中文\n该社...\nSkip to PDF content\n"
    assert summary_en(page) == "The club was admitted in April 1932."
    assert squash("President of the\n  Tientsin Rotary Club – 1930") in squash("He was President of the Tientsin Rotary Club — 1930.")
    assert squash("出生於大清國\n河南省") == "出生於大清國河南省"
    assert club_key("Rotary Club of Shanghai West") == club_key("Shanghai West Rotary Club (滬西扶輪社)") == "shanghai west"
    cs = chunks([(1, "A sentence. " * 300), (2, "郭克悌出生於河南省。" * 200)], 2000)
    assert all(len(c) <= 2100 for c in cs) and squash("".join(cs)) == squash("A sentence. " * 300 + "郭克悌出生於河南省。" * 200)
    got = Statements.model_validate({"statements": [{"subject": "X", "subject_type": "family", "attribute": "spouse", "value": None, "quote": "q"}]}).statements[0]
    assert (got.subject_type, got.attribute, got.detail, got.value) == ("person", "other", "spouse", "")
