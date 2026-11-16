"""Record structures (the fields of the XMind plan), as validated pydantic models.

`Enrichment` is what the language model (or a human annotator) fills in.
`Document` = identifiers + Enrichment + full_text assembled by code from page texts.
The model never writes full_text, which is how "verbatim, not regenerated" is guaranteed.
"""
from __future__ import annotations

import copy
import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

Status = Literal["specified", "inferred", "uncertain", "not_specified", "not_applicable"]
DATE_PATTERN = r"^\d{4}(-\d{2}(-\d{2})?)?$"  # YYYY, YYYY-MM or YYYY-MM-DD


def clean_date(v):
    """Keep the valid leading part of a date: '1936-07-00' -> '1936-07', '1936-07-03T00:00' -> '1936-07-03',
    'July 1936' -> None (the wording stays in as_written)."""
    if v is None:
        return None
    m = re.match(r"^\s*(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?", str(v))
    if not m:
        return None
    y, mo, d = m.groups()
    if not (mo and 1 <= int(mo) <= 12):
        mo = d = None
    if not (d and 1 <= int(d) <= 31):
        d = None
    return "-".join([y] + ([f"{int(mo):02d}"] if mo else []) + ([f"{int(d):02d}"] if d else []))


def _named(v):
    """'Y. Gundsi' -> {'name': 'Y. Gundsi'}; entries without a name are dropped."""
    out = []
    for x in v or []:
        if isinstance(x, str):
            x = {"name": x}
        if isinstance(x, dict) and str(x.get("name") or "").strip():
            out.append(x)
    return out


class Qualified(BaseModel):
    """A value plus how sure we are: lets 'not specified / not applicable / uncertain' be queried."""
    value: Optional[str] = None
    status: Status = "not_specified"


class DateInfo(BaseModel):
    type: Literal["produced", "received", "modified", "other"] = Field(
        description="produced = written/sent/issued; received = e.g. receipt stamp; "
        "modified = e.g. date an excerpt or copy was made; other = any other date role")
    value: Optional[str] = Field(None, pattern=DATE_PATTERN,
                                 description="ISO date; truncate to YYYY-MM or YYYY if day/month unknown")
    as_written: Optional[str] = Field(None, description="the date exactly as it appears on the page")
    certainty: Literal["certain", "uncertain"] = "certain"

    _clean = field_validator("value", mode="before")(clean_date)


class Agent(BaseModel):
    name: str = Field(description="as written in the document")
    role: Optional[str] = None
    kind: Literal["person", "organization", "unknown"] = "person"


class Entity(BaseModel):
    name: str = Field(description="as written in the document")
    role: Optional[str] = None
    normalized: Optional[str] = Field(None, description="standard form if confidently known, else null")


class Entities(BaseModel):
    persons: list[Entity] = []
    organizations: list[Entity] = []
    locations: list[Entity] = []
    events: list[Entity] = []
    works: list[Entity] = []

    _named = field_validator("persons", "organizations", "locations", "events", "works", mode="before")(_named)


class Illustration(BaseModel):
    page: int
    alt_text: str


class TableMD(BaseModel):
    page: int
    caption: Optional[str] = None
    markdown: str = Field(description="the table transcribed as a Markdown table, cell text verbatim")


class Item(BaseModel):
    """One of several separate items on a single sheet (press clippings, pasted slips)."""
    label: str = Field(description="headline or short description of the item")
    source: Optional[str] = Field(None, description="newspaper or issuing body, as written")
    date: Optional[str] = Field(None, pattern=DATE_PATTERN)
    date_as_written: Optional[str] = None
    _clean = field_validator("date", mode="before")(clean_date)
    page: Optional[int] = None
    first_words: Optional[str] = Field(None, description="its first 6-10 words, verbatim, to locate it in the text")


class EnrichCore(BaseModel):
    """Everything about a document except its named entities (asked for in a separate, text-only call)."""
    genre: str
    title: Qualified
    dates: list[DateInfo] = []
    place_origin: Qualified = Qualified()
    place_destination: Qualified = Qualified()
    creators: list[Agent] = []
    recipients: list[Agent] = []
    languages: list[str] = Field(default=[], description="ISO 639-1 codes, e.g. en, zh, ja, ru, fr")
    summary: str = Field(description="in English")
    has_illustration: bool = False
    illustrations: list[Illustration] = []
    has_table: bool = False
    tables: list[TableMD] = []
    items: list[Item] = Field(default=[], description="only for sheets carrying several separate items; else empty")

    @model_validator(mode="before")
    @classmethod
    def _tolerant(cls, d):
        if not isinstance(d, dict):
            return d
        d = dict(d)
        for k in ("title", "place_origin", "place_destination"):   # a bare string instead of {value, status}
            if isinstance(d.get(k), str):
                d[k] = {"value": d[k], "status": "specified"}
            elif d.get(k) is None and k in d:
                d[k] = {"value": None, "status": "not_specified"}
        for k in ("dates", "illustrations", "tables", "items", "languages"):  # null instead of an empty list
            if d.get(k) is None and k in d:
                d[k] = []
        if isinstance(d.get("languages"), str):
            d["languages"] = [x.strip() for x in re.split(r"[,;/ ]+", d["languages"]) if x.strip()]
        for k in ("creators", "recipients"):
            if k in d:
                d[k] = _named(d[k])
        if d.get("entities") is None and "entities" in d:
            d["entities"] = {}
        if d.get("summary") is None:
            d["summary"] = ""
        if "title" not in d:   # asked for, but a record without it is still worth keeping
            d["title"] = {"value": None, "status": "not_specified"}
        return d
    notes: Optional[str] = Field(None, description="uncertainties, legibility problems, stamps, annotations")


class Enrichment(EnrichCore):
    entities: Entities = Entities()


class Document(Enrichment):
    doc_id: str
    file_number: str
    pages: list[int]
    start_page: int
    end_page: int
    full_text: str
    page_offsets: dict[str, int] = Field(description="character offset in full_text where each page starts")
    review_status: Literal["unreviewed", "reviewed"] = "unreviewed"
    provenance: dict = {}


class PageLabel(BaseModel):
    page: int
    reason: Optional[str] = Field(None, description="one short sentence: what on the page shows it (written before deciding)")
    relation: Literal["new", "continues", "continues_earlier", "blank"]
    continues_page: Optional[int] = Field(None, description="only for continues_earlier: the earlier page this one belongs with")
    note: Optional[str] = None


PAGE_KINDS = ["letter", "form", "roster_or_list", "bulletin", "clipping", "certificate", "record_card",
              "cover_or_contents", "regulations", "programme", "photograph", "other"]


class PageFacts(BaseModel):
    """What can be seen on one page, without reference to its neighbours (segment.method: features)."""
    is_blank: bool = False
    page_kind: Literal["letter", "form", "roster_or_list", "bulletin", "clipping", "certificate", "record_card",
                       "cover_or_contents", "regulations", "programme", "photograph", "other"] = "other"
    heading: Optional[str] = None
    has_letterhead: bool = False
    date_line: Optional[str] = None
    has_salutation: bool = False
    begins_mid_text: bool = False
    printed_page_number: Optional[int] = None
    looks_like_reverse_side: bool = False
    has_closing_signature: bool = False

    @field_validator("printed_page_number", mode="before")
    @classmethod
    def _int(cls, v):
        m = re.search(r"\d+", str(v)) if v is not None else None
        return int(m.group()) if m else None


class PairLabel(BaseModel):
    reason: str = Field(description="one short sentence: what on the two pages shows it")
    relation: Literal["new", "continues", "blank"]


class Segmentation(BaseModel):
    pages: list[PageLabel]


class Transcription(BaseModel):
    text: str = Field(description="verbatim transcription in reading order")
    languages: list[str] = []
    is_blank: bool = False
    illegible_note: Optional[str] = None


class Citation(BaseModel):
    source: str = Field(description="source label, e.g. S2")
    quote: str = Field(description="short passage copied exactly from that source")


class RagAnswer(BaseModel):
    answer: str
    citations: list[Citation] = []


def require_all(node):
    """For schemas sent to a model: every field must be filled in (with null or an empty list where
    nothing applies). Optional fields are what smaller models skip first - names of persons included."""
    if isinstance(node, dict):
        node = {k: require_all(v) for k, v in node.items()}
        if isinstance(node.get("properties"), dict):
            node["required"] = list(node["properties"])
        return node
    if isinstance(node, list):
        return [require_all(v) for v in node]
    return node


def json_schema(model: type[BaseModel], genres: list[str] | None = None) -> dict:
    """JSON Schema with $refs inlined (most LLM tool/JSON-schema interfaces prefer that)."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def inline(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return inline(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            # drop the "title" annotation (a string) but keep a field that is itself called title (a definition)
            return {k: inline(v) for k, v in node.items() if not (k == "title" and isinstance(v, str))}
        if isinstance(node, list):
            return [inline(v) for v in node]
        return node

    schema = inline(schema)
    if genres and "genre" in schema.get("properties", {}):
        schema["properties"]["genre"]["enum"] = list(genres)
    return schema
