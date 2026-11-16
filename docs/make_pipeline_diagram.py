"""Builds docs/pipeline_diagram.html: the overview with icons, and the detailed version."""
from pathlib import Path
import html

ICONS = {
 "scan": "M6 3h9l4 4v14H6z M15 3v4h4 M9 12h7 M9 16h7",
 "notes": "M5 4h12a2 2 0 0 1 2 2v14H7a2 2 0 0 1-2-2z M9 8h6 M9 12h6",
 "globe": "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z M3 12h18 M12 3c3 3 3 15 0 18 M12 3c-3 3-3 15 0 18",
 "eye": "M2 12c3-5 7-7 10-7s7 2 10 7c-3 5-7 7-10 7s-7-2-10-7z M12 9a3 3 0 1 0 0 6a3 3 0 1 0 0-6z",
 "split": "M4 5h16 M4 9h16 M4 15h16 M4 19h16 M2 12h3 M8 12h3 M14 12h3 M20 12h2",
 "tag": "M3 12V4h8l10 10-8 8z M7.5 7a1.5 1.5 0 1 0 0 3a1.5 1.5 0 1 0 0-3z",
 "merge": "M4 6h5l6 6h5 M4 18h5l6-6 M17 9l3 3-3 3",
 "db": "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3v12c0 1.7-3.6 3-8 3s-8-1.3-8-3z M4 6c0 1.7 3.6 3 8 3s8-1.3 8-3 M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
 "search": "M10 4a6 6 0 1 0 0 12a6 6 0 1 0 0-12z M15 15l6 6",
 "import": "M12 3v11 M8 10l4 4 4-4 M4 16v4h16v-4",
 "user": "M12 4a4 4 0 1 0 0 8a4 4 0 1 0 0-8z M4 21c0-4 4-6 8-6s8 2 8 6",
 "club": "M4 21V8l8-5 8 5v13 M9 21v-6h6v6 M2 21h20",
 "quote": "M5 5h14v10H10l-5 4z M9 9h6 M9 12h4",
 "pencil": "M4 20l1-5L16 4l4 4L9 19z M14 6l4 4",
 "window": "M3 5h18v14H3z M3 9h18 M6 7h.01",
 "code": "M8 7l-5 5 5 5 M16 7l5 5-5 5 M13 5l-2 14",
 "chart": "M4 20V4 M4 20h16 M8 16v-5 M12 16V8 M16 16v-9",
 "link": "M10 14a4 4 0 0 0 6 0l3-3a4 4 0 0 0-6-6l-1 1 M14 10a4 4 0 0 0-6 0l-3 3a4 4 0 0 0 6 6l1-1",
 "check": "M4 12l5 5L20 6",
 "list": "M8 6h12 M8 12h12 M8 18h12 M4 6h.01 M4 12h.01 M4 18h.01",
 "image": "M4 5h16v14H4z M4 16l5-5 4 4 3-3 4 4",
}
e = html.escape
def icon(name, x, y, size=20, cls="ic"):
    return f'<use href="#i-{name}" x="{x}" y="{y}" width="{size}" height="{size}" class="{cls}"/>'
def defs(prefix=""):
    syms = "".join(f'<symbol id="i-{k}" viewBox="0 0 24 24"><path d="{d}"/></symbol>' for k, d in ICONS.items())
    return ('<defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
            '<path d="M2 1L8 5L2 9" fill="none" stroke="#5f5e5a" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></marker>' + syms + '</defs>')

# ------------------------------------------------------------------ 1. overview
def card(x, y, w, h, ic, title, sub, cls="box"):
    return (f'<g><rect class="{cls}" x="{x}" y="{y}" width="{w}" height="{h}" rx="8"/>{icon(ic, x + 12, y + h/2 - 10)}'
            f'<text class="th" x="{x + 42}" y="{y + h/2 - 9}" dominant-baseline="central">{e(title)}</text>'
            f'<text class="ts" x="{x + 42}" y="{y + h/2 + 9}" dominant-baseline="central">{e(sub)}</text></g>\n')
def overview():
    o = [f'<svg viewBox="0 0 760 590" role="img" xmlns="http://www.w3.org/2000/svg"><title>Rotary in China pipeline: overview</title>{defs()}\n']
    o.append("<!-- sources -->\n")
    for x, ic, t, s in ((40, "scan", "Archive scans", "63 folders, pages and OCR"), (280, "notes", "Your research notes", "Word and Excel per club"),
                        (520, "globe", "rotaryinchina.org", "Articles and their PDFs")):
        o.append(card(x, 40, 200, 56, ic, t, s))
    o.append('<line class="arr" x1="140" y1="96" x2="140" y2="128" marker-end="url(#arrow)"/>'
             '<path class="arr" d="M380 96 L380 113 L500 113 L500 128" marker-end="url(#arrow)"/>'
             '<line class="arr" x1="620" y1="96" x2="620" y2="128" marker-end="url(#arrow)"/>\n')
    for x, cls, t, s, steps in (
        (40, "teal", "Text database", "One record per document",
         (("eye", "Transcribe", "Vision model corrects the OCR"), ("split", "Segment", "Pages grouped into documents"),
          ("tag", "Enrich", "Title, genre, dates, entities"), ("merge", "Normalise", "Name variants merged"), ("db", "Build and index", "Tables, checks, search index"))),
        (460, "purple", "Actor database", "Every fact with its source",
         (("import", "Ingest", "Notes become statements"), ("user", "Resolve", "Name variants become persons"),
          ("club", "Describe clubs", "Names, dates, meetings"), ("quote", "Read the web articles", "Quoted, checked, secondary"), ("db", "Build", "Tables, graph, map data")))):
        o.append(f'<!-- {t} -->\n<g class="{cls}"><rect x="{x}" y="130" width="260" height="336" rx="16"/><text class="th" x="{x+130}" y="154" text-anchor="middle" dominant-baseline="central">{t}</text>'
                 f'<text class="ts" x="{x+130}" y="174" text-anchor="middle" dominant-baseline="central">{s}</text></g>\n')
        for i, (ic, a, b) in enumerate(steps):
            o.append(card(x + 12, 194 + 52 * i, 236, 44, ic, a, b))
    o.append('<!-- corrections and the link between the databases -->\n<g class="coral"><rect x="316" y="222" width="128" height="100" rx="8"/>'
             + icon("pencil", 370, 232, 20, "ic coral-ic") +
             '<text class="th" x="380" y="266" text-anchor="middle" dominant-baseline="central">Your corrections</text>'
             '<text class="ts" x="380" y="288" text-anchor="middle" dominant-baseline="central">Always win</text></g>\n'
             '<line class="arr" x1="316" y1="272" x2="303" y2="272" marker-end="url(#arrow)"/><line class="arr" x1="444" y1="272" x2="457" y2="272" marker-end="url(#arrow)"/>'
             '<line class="arr" x1="300" y1="424" x2="457" y2="424" marker-end="url(#arrow)"/>'
             '<text class="ts" x="380" y="412" text-anchor="middle">Documents cited</text><text class="ts" x="380" y="442" text-anchor="middle">as sources</text>\n<!-- ways in -->\n')
    for x, ic, t, s in ((40, "window", "Text viewer", "Read, search, ask"), (216, "code", "Python and R", "Query the tables"),
                        (392, "chart", "Actor viewer", "Charts, map, graph"), (568, "link", "Public browser", "Permanent link")):
        o.append(card(x, 514, 152, 56, ic, t, s))
    for x1 in (116, 270, 490, 644):
        o.append(f'<line class="arr" x1="{x1}" y1="466" x2="{x1}" y2="512" marker-end="url(#arrow)"/>')
    return "".join(o) + "\n</svg>"

# ------------------------------------------------------------------ 2. detailed version
#   (icon, name, what it does, where: gpu|mac|both, status: real|standin|rules|partly, status note)
RCTB = [
 ("list", "Inventory", "Archive index and page folders become the list of files", "mac", "rules", ""),
 ("image", "Pages", "Page images; text layers from Google Vision, Tesseract, the PDF", "mac", "rules", ""),
 ("eye", "Transcribe", "A vision model corrects the OCR against the page image", "gpu", "real", "44 of 46 sample pages"),
 ("split", "Segment", "The model describes each page; fixed rules place the boundaries", "gpu", "partly", "New method: stand-in only"),
 ("tag", "Enrich", "One call for title, genre, dates, summary; one for entities", "gpu", "partly", "Two-call version: stand-in only"),
 ("merge", "Normalise", "Rules propose merges of name variants; you confirm in entities.csv", "mac", "rules", ""),
 ("db", "Build", "Text assembled from pages by code; tables, checks, QA report", "mac", "rules", ""),
 ("search", "Index", "Passages embedded for meaning-based search and the Ask tab", "both", "real", "bge-m3 beat the word-count baseline"),
 ("check", "Evaluate", "Boundaries and fields compared with a reference file", "mac", "rules", "Reference not yet reviewed"),
]
RCAD = [
 ("globe", "Harvest", "Copy of rotaryinchina.org: pages, text, PDFs, checksums", "mac", "rules", "674 pages harvested"),
 ("import", "Ingest", "Word notes and Excel tables become statements with page references", "mac", "rules", "Run on Harbin"),
 ("user", "Resolve", "Name variants become persons and institutions with permanent IDs", "mac", "rules", "12 suggestions to review"),
 ("club", "Describe clubs", "Names, charter number, admissions, terminations, meetings", "mac", "rules", ""),
 ("quote", "Read the web articles", "A model fills a form per passage; each quote is checked in the text", "gpu", "standin", "No real-model run yet"),
 ("db", "Build", "Tables with declared links, CSV, graph, map data", "mac", "rules", "Cites text-database documents as sources"),
]
WHERE = {"gpu": ("Huma-Num GPU", "p-gpu"), "mac": ("Your Mac", "p-mac"), "both": ("Either", "p-mac")}
STATUS = {"real": ("Run with the real model", "s-real"), "standin": ("Stand-in model only", "s-standin"),
          "partly": ("Partly re-tested", "s-partly"), "rules": ("Fixed rules, no model", "s-rules")}
def step(x, y, w, ic, name, what, where, status, note):
    wl, wc = WHERE[where]; sl, sc = STATUS[status]
    return (f'<g><rect class="box{" gpu" if where == "gpu" else ""}" x="{x}" y="{y}" width="{w}" height="64" rx="8"/>{icon(ic, x + 14, y + 12, 22)}'
            f'<text class="th" x="{x + 48}" y="{y + 18}" dominant-baseline="central">{e(name)}</text>'
            f'<text class="ts" x="{x + 48}" y="{y + 37}" dominant-baseline="central">{e(what)}</text>'
            f'<g class="{wc}"><rect x="{x + w - 118}" y="{y + 8}" width="106" height="20" rx="10"/><text class="tp" x="{x + w - 65}" y="{y + 18}" text-anchor="middle" dominant-baseline="central">{wl}</text></g>'
            f'<circle class="{sc}" cx="{x + 53}" cy="{y + 52}" r="4"/><text class="tn" x="{x + 63}" y="{y + 52}" dominant-baseline="central">{e(sl + (" · " + note if note else ""))}</text></g>\n')
def detailed():
    W, CW = 1200, 540
    o = [f'<svg viewBox="0 0 {W} 944" role="img" xmlns="http://www.w3.org/2000/svg"><title>Rotary in China pipeline: detailed version</title>{defs()}\n']
    # legend
    o.append('<!-- legend -->\n<g class="p-gpu"><rect x="40" y="24" width="106" height="20" rx="10"/><text class="tp" x="93" y="34" text-anchor="middle" dominant-baseline="central">Huma-Num GPU</text></g>'
             '<text class="tn" x="154" y="34" dominant-baseline="central">a model reads pages or text</text>'
             '<g class="p-mac"><rect x="340" y="24" width="106" height="20" rx="10"/><text class="tp" x="393" y="34" text-anchor="middle" dominant-baseline="central">Your Mac</text></g>'
             '<text class="tn" x="454" y="34" dominant-baseline="central">fixed rules, no model</text>')
    lx = 620
    for key in ("real", "partly", "standin", "rules"):
        lab, cls = STATUS[key]
        o.append(f'<circle class="{cls}" cx="{lx}" cy="34" r="4"/><text class="tn" x="{lx + 10}" y="34" dominant-baseline="central">{lab}</text>')
        lx += 22 + len(lab) * 6.3
    o.append("\n")
    for x, cls, title, sub, steps, src in ((40, "teal", "Text database", "python -m rctb …   ·   one record per document", RCTB, ("scan", "Archive scans: 63 folders of page images and OCR text")),
                                           (620, "purple", "Actor database", "python -m rcad …   ·   every fact with its source", RCAD, ("notes", "Your notes per club (Word, Excel) and rotaryinchina.org"))):
        h = 76 + 72 * len(steps) + 6
        o.append(f'<!-- {title} -->\n<g><rect class="box" x="{x}" y="60" width="{CW}" height="40" rx="8"/>{icon(src[0], x + 14, 70)}<text class="th" x="{x + 44}" y="80" dominant-baseline="central">{e(src[1])}</text></g>'
                 f'<line class="arr" x1="{x + CW/2}" y1="100" x2="{x + CW/2}" y2="116" marker-end="url(#arrow)"/>'
                 f'<g class="{cls}"><rect x="{x}" y="118" width="{CW}" height="{h}" rx="16"/><text class="th" x="{x + 20}" y="142" dominant-baseline="central">{title}</text>'
                 f'<text class="ts mono" x="{x + 20}" y="162" dominant-baseline="central">{e(sub)}</text></g>\n')
        for i, s in enumerate(steps):
            o.append(step(x + 14, 182 + 72 * i, CW - 28, *s))
    # right column: under the actor database (ends at 118+76+432+6=632)
    y = 650
    o.append('<!-- your corrections -->\n<g class="coral"><rect x="620" y="%d" width="540" height="76" rx="12"/>%s<text class="th" x="664" y="%d" dominant-baseline="central">Your corrections always win</text>'
             '<text class="ts" x="664" y="%d" dominant-baseline="central">Page text, boundaries, entity merges (corrections/) · persons, institutions,</text>'
             '<text class="ts" x="664" y="%d" dominant-baseline="central">clubs, places, web matches (actors/authority/). Never overwritten by a run.</text></g>\n'
             % (y, icon("pencil", 634, y + 12, 22, "ic coral-ic"), y + 20, y + 42, y + 58))
    o.append('<!-- link between the databases: text database Build -> actor database Build -->\n<path class="arr" d="M580 646 L600 646 L600 574 L618 574" marker-end="url(#arrow)"/>\n')
    # ways in
    o.append("<!-- ways in -->\n")
    for x, y, ic, t, s in ((40, 868, "window", "Text viewer", "Image beside text, search, Ask"), (320, 868, "code", "Python and R", "SQLite, Parquet, CSV"),
                           (620, 762, "chart", "Actor viewer", "Statistics, map, network"), (900, 762, "link", "Public browser", "Datasette; hosting still to set up")):
        o.append(f'<g><rect class="box" x="{x}" y="{y}" width="260" height="56" rx="8"/>{icon(ic, x + 12, y + 18)}<text class="th" x="{x + 42}" y="{y + 19}" dominant-baseline="central">{t}</text>'
                 f'<text class="ts" x="{x + 42}" y="{y + 37}" dominant-baseline="central">{e(s)}</text></g>'
                 + (f'<line class="arr" x1="{x + 130}" y1="{y - 20}" x2="{x + 130}" y2="{y - 2}" marker-end="url(#arrow)"/>' if y == 868 else ""))
    return "".join(o) + "\n</svg>"

CSS = """
  body { margin: 28px; background: #ffffff; font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; color: #2c2c2a; }
  h1 { font-size: 22px; font-weight: 500; margin: 0 0 4px; }
  h2 { font-size: 18px; font-weight: 500; margin: 44px 0 4px; }
  p  { font-size: 14px; color: #5f5e5a; margin: 0 0 14px; max-width: 900px; line-height: 1.5; }
  svg { display: block; width: 100%; }
  svg.small { max-width: 860px; }  svg.wide { max-width: 1240px; }
  .th { font-size: 14px; font-weight: 500; fill: #2c2c2a; }
  .ts { font-size: 12px; fill: #5f5e5a; }
  .tn { font-size: 11.5px; fill: #5f5e5a; }
  .tp { font-size: 11px; font-weight: 500; }
  .mono { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 11.5px; }
  .box { fill: #ffffff; stroke: #b4b2a9; stroke-width: 0.75; }
  .box.gpu { stroke: #993c1d; stroke-width: 1.25; }                 /* steps where a model works */
  .arr { stroke: #5f5e5a; stroke-width: 1.5; fill: none; }
  .ic  { fill: none; stroke: #444441; stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; }
  .coral-ic { stroke: #712b13; }
  .teal > rect   { fill: #e1f5ee; stroke: #0f6e56; stroke-width: 0.75; }  .teal > .th   { fill: #085041; }  .teal > .ts   { fill: #0f6e56; }
  .purple > rect { fill: #eeedfe; stroke: #534ab7; stroke-width: 0.75; }  .purple > .th { fill: #3c3489; }  .purple > .ts { fill: #534ab7; }
  .coral > rect  { fill: #faece7; stroke: #993c1d; stroke-width: 0.75; }  .coral > .th  { fill: #712b13; }  .coral > .ts  { fill: #993c1d; }
  .p-gpu rect { fill: #993c1d; }                       .p-gpu text { fill: #ffffff; }
  .p-mac rect { fill: #f1efe8; stroke: #b4b2a9; stroke-width: 0.75; }  .p-mac text { fill: #444441; }
  .s-real    { fill: #0f6e56; }                         /* status dots: filled = a real model has run it */
  .s-partly  { fill: #ffffff; stroke: #0f6e56; stroke-width: 2.5; }
  .s-standin { fill: #ffffff; stroke: #993c1d; stroke-width: 2.5; }
  .s-rules   { fill: #b4b2a9; }
"""
HOWTO = """<!--
  HOW TO EDIT
  - Open this file in a browser to see it; edit it in VS Code and reload.
  - Each box is a <g> block: a <rect> (x, y = top-left corner; width, height), an icon (<use href="#i-...">) and <text> lines.
    To rename a box, change the words between <text ...> and </text>. Text does not wrap: add a second <text> for a new line.
  - Colours and line weights are set once, in the <style> block above.
  - Icons are defined once per drawing, in <defs>, as <symbol id="i-name">; to swap an icon change href="#i-name".
    Available: scan notes globe eye split tag merge db search import user club quote pencil window code chart link check list image
  - Detailed version: the pill (Huma-Num GPU / Your Mac) and the status line under each step are plain text and classes:
    p-gpu / p-mac for the pill; s-real / s-partly / s-standin / s-rules for the dot. Update them as runs are done.
  - To use a drawing elsewhere: print to PDF, or copy one <svg>...</svg> block together with the <style> rules into its own file.
-->"""
page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Rotary in China pipeline</title>
<style>{CSS}</style>
{HOWTO}
</head><body>
<h1>Rotary in China pipeline</h1>
<p>Three kinds of sources, two linked databases, four ways in.</p>
{overview().replace('<svg ', '<svg class="small" ', 1)}

<h2>Detailed version: where each step runs, and how far it has been tested</h2>
<p>State on 16 November 2026. Steps outlined in red are the ones a model performs; they run on the Huma-Num GPUs. Everything else is fixed rules and runs on your Mac in seconds.</p>
{detailed().replace('<svg ', '<svg class="wide" ', 1)}
</body></html>
"""
(Path(__file__).with_name("pipeline_diagram.html")).write_text(page, encoding="utf-8")
