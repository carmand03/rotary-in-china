# Rotary in China Actor Database (RCAD)

Persons, clubs and institutions of the Rotary movement in China and Manchuria, built from your
per-club workbooks and notes and linked, page by page, to the text database (RCTB).

Status: the pipeline runs end to end on the Harbin sample (one workbook, one notes file). It has not
seen another club's files yet, and events, places with coordinates and external web sources are
designed for but not implemented.

## Principle: two layers

| Layer | What it holds | Who writes it |
|---|---|---|
| **Attestations** | One row per statement in one source, exactly as recorded: "the roster on B37-F01 p. 4 lists Ahnert, E. E., Mining Engineer, Mulin Coal Mining Co." | You, in the workbooks; the pipeline copies them unchanged |
| **Entities** | Persons, institutions, committees, positions, classifications: who and what the attestations are about | Rules propose; you decide, in the authority tables |

Contradictions between sources are therefore kept, not resolved away: two rosters giving two
classifications for the same man are two attestation rows, each with its date and source.
Everything derived (a person's first and last attested year, the membership summary) is recomputed
at each build and never edited.

## Pipeline

```
actors/sources/   Harbin.xlsx, Harbin.docx, ...     your files, one pair per club (never modified)
      │  python -m rcad ingest
actors/work/      attestation CSVs                  regenerated
      │  python -m rcad resolve
actors/authority/ persons.csv, institutions.csv, committees.csv, positions.csv, position_map.csv,
                  classifications.csv, places.csv   proposed by rules, edited by you, kept for good
      │  python -m rcad build      (+ data/out/rctb.sqlite, the text database)
actors/out/       rcad.sqlite, csv/*.csv            the database, rebuilt from scratch each time
                  graph/nodes.csv, edges.csv        the same data as a graph (Gephi, igraph, networkx, Neo4j)
```

`python -m rcad all` runs the three stages. Adding a club = dropping its workbook and notes into
`actors/sources/` and running it again. Adding rows to an existing workbook works the same way.

### Expected input

- `<Club>.xlsx` with sheets `members`, `officers`, `mbr_years`, `mbr_month`, `attend_month`, with the column
  headers of the Harbin file. The `Source` column holds archive references written `B37-F01-04`
  (box, folder, page), several separated by commas.
- `<Club>.docx`: a header (free lines), then for each folder a line with its number (`B36-F05`) followed
  by notes that start with their pages (`p.11-12 : ...`). Paragraphs without page numbers continue the
  note above.
- The file name must be the club's name as in the archive index (`Harbin`, `Tientsin`).

### Checking your files

`ingest` writes `actors/work/input_report.md`: sheets and columns it did not recognise, rows without a
source, source references not in the form `B37-F01-04`, notes without page numbers or with page numbers
out of sequence. It reports and changes nothing; read it after adding each club.

## Identifiers are permanent

A person receives a `person_id` (`P00027`) the first time one of their name variants is seen. The
pipeline never changes it afterwards; new spellings found later are attached to it when the evidence
is strong, or get an identifier of their own with the probable match shown in `suggested_id`.

To merge two persons, give both rows the same `person_id` in `actors/authority/persons.csv` and set
`status` to `confirmed`. The retired identifier keeps working: the build records it in
`person_redirect`. To refuse a suggestion, set `status` to `rejected`.

| Rule (column `rule`) | Status |
|---|---|
| same name in Chinese/Japanese characters | auto |
| same family name, compatible given name or initials (Gundsi, Y. = Gundsi, Yoshio) | auto |
| similar family name (Kowasaki / Kowazaki), or given name missing | suggested |
| several possible matches (Takahashi, K. when two Takahashi K... exist) | suggested |

Matching is done within one club. The same man in two clubs will appear as two persons until you
merge them.

## Tables (`actors/out/rcad.sqlite`)

| Table | Content |
|---|---|
| `person`, `person_name`, `person_redirect` | persons; every spelling with the rule that attached it; retired identifiers |
| `club`, `club_statement`, `club_period` | one line per club (summary); every statement on a club's names, charter number, dates, meetings, language and publication, with period and source; periods of existence (admission to termination) |
| `club_fact`, `club_stat` | header lines of your notes as written; membership and attendance figures (one value per row) |
| `institution` | companies and bodies members belonged to |
| `membership_attestation` | one row per member listed in one source: classification, institution, position, addresses, election date, charter member |
| `office_attestation` (view `office`) | one row per office or committee seat stated in one source |
| `membership` | one row per person and club, summarising the attestations |
| `position`, `committee`, `classification` | the three dictionaries |
| `source`, `attestation_source` | archive pages cited, each linked to the RCTB document containing it |
| `document` | RCTB documents (copied in, so that links work inside one database) |
| `note`, `note_document` | your notes, linked to the documents on the pages they describe |
| `person_document` | RCTB documents in which a person is named, matched from the text database's entities |
| `place` | club cities; coordinates to be filled in `actors/authority/places.csv` |

## Browsing and publishing

`streamlit run app/actors.py` opens the viewer: persons, clubs, institutions, places and events in
tabs, statistics (membership and attendance over time, joiners and leavers, classifications), a
network view and a map. The sidebar searches names and filters by club, classification and years,
for every tab at once. It reads `actors/out/`, so run `python -m rcad all` first.

For publication on the web, the same database can be served with Datasette:

`python -m rcad serve` prints the command that opens the database in Datasette: search, filters by
club / year / position / classification, one page per person with everything linked to it, and CSV or
JSON export of any table or search result.

## What you need to decide or supply

1. **Classifications**: `actors/authority/classifications.csv` lists every wording found; the `major` and
   `minor` columns are empty and need the Rotary classification outline.
2. **Suggestions** in `persons.csv` and `institutions.csv`.
3. **Coordinates** in `places.csv`.
4. **Citation form** for archive pages (now: collection, box, folder, page).

## Club identification

A club's name, admissions, terminations, meeting day or language can change over time, so each is a
row of `actors/authority/club_statements.csv` (one statement, its period, its source), and the
`club` table only summarises them.

| attribute | value | detail |
|---|---|---|
| `name` | a name of the club; add a row per alternative name (Peiping), with `date_from` / `date_to` | |
| `charter_number` | the RI charter number | |
| `organized`, `chartered`, `admitted`, `terminated` | a date (`1930-04-24`, `1930-04` or `1930`); one row per admission and per termination | |
| `meeting_day`, `meeting_time`, `meeting_place` | one row per value, with `date_from` / `date_to` when it changed | |
| `language` | one row per language | |
| `publication` | the title, or `no` if you know there was none | periodicity |

Rows with status `auto` are read at every build from the archive index (dates) and from the lines
before the first folder in your notes, when they look like this:

    Harbin, Manchuria (No. 3334)          Languages: English, Russian
    Organized 24 April 1930               Publication: The Pagoda (weekly)
    Admitted 4 June 1930                  Also known as: Peiping (1928-1949)
    Terminated 31 December 1940           Readmitted 14 August 1946
    Meetings Thursday 1pm, Nichiman Club

Your own rows (any other status, or none) are never touched. To refuse an `auto` row, set its status
to `rejected` and add the right one. The archive index has a single CHARTERED column with one date
per folder: the earliest is taken as the charter date and each distinct date as an admission to RI.

Today's city and province (pinyin and characters) are columns of `places.csv`; the page count of a
club is the sum over its folders of the pages counted in the scans, or else of the index's figure.

## Web sources (rotaryinchina.org)

    python scripts/harvest_rotaryinchina.py         copy of the site's pages and PDFs   -> actors/web/rotaryinchina/
    python -m rcad web --config <config with a model> [--limit 20] [--pages fitch canton]
    python -m rcad build

`web` reads each article and records what it states about persons, clubs, institutions and events
as statements: subject, attribute (birth, education, occupation, rotary_office, relation, admitted...),
value, dates, place, and the passage that supports it. The form and its rules are in
`prompts/web_statements.md`.

- **What is read.** The article proper is the PDF attached to each page; statements come from the
  PDF text, with the PDF page number. The page's own three-language summary is used only when there
  is no PDF. (Those summaries are not reliable for Chinese names: for the 1937 Nanking conference
  the summary writes 方福锡 for Fong Foo-Sec, where the article itself has 鄺富灼.)
- **Which pages.** A page is read when it names a club of the archive and has at least two dates
  between 1919 and 1952. `actors/work/web_pages.csv` gives the decision and its reason for every
  page; put `yes` or `no` in the `use` column of `actors/authority/web_pages.csv` to overrule it.
- **Checks.** A statement is kept only if its quoted passage is found in the article word for word
  and, for a person, if that person is named in or shortly before the passage. The others go to
  `actors/work/web_rejected.csv` with the reason. The check catches invented facts; it does not
  catch facts the model overlooked.
- **In the database.** Tables `web_page` (also a row of `source`, type `web`), `web_subject` and
  `web_statement`, all labelled `secondary`. Nothing from the web is written into the archive-based
  tables. Clubs are linked by name. Persons are linked automatically only when the name in characters
  is identical; other likely matches are `suggested` in `actors/authority/web_subjects.csv` and
  apply once you set them to `confirmed` (or fill `match_id` yourself and set `confirmed`).
- **Model.** Uses `llm` of the config; to change model or passage length for this stage only:
  `llm: {stages: {web: {model: "...", chunk_chars: 5000}}}`. Calls are cached, so an interrupted
  run continues where it stopped and a re-run costs nothing for unchanged pages.
