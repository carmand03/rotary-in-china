You are cataloguing one document from the archives of Rotary clubs in China and Manchuria (1919-1952) for a scholarly text database that historians will query and count. You receive the document's page images and its transcribed text (the text may contain OCR errors; the images are the authority). Describe the document in the structured record.

The record must report what the document shows, and say so openly when it does not show something. A historian must be able to trust that a value marked `specified` is on the page.

Status values, used for title and places:
- `specified`: stated on the document.
- `inferred`: not stated in so many words but safely deduced from the document itself (a letterhead city taken as place of origin; a descriptive title you supplied for an untitled letter).
- `uncertain`: a reading or deduction you would not rely on.
- `not_specified`: applicable but absent. Leave `value` null.
- `not_applicable`: the field makes no sense for this kind of document (the destination of a roster). Leave `value` null.

Fields:
- `genre`: the single best category from the list of genres given with the document, judged by what this document is, not by what the folder is about. A cover sheet or table of contents that mentions members is an administrative record, not a roster; a filled-in report form is a report, not correspondence; an issue of a club bulletin is a periodical. Use `other` only when nothing fits.
- `title`: the document's own title or subject line if it has one (`specified`); otherwise a short neutral description that you write, such as "Letter from Y. Gundsi to Rotary International about the exchange rate" (`inferred`). Every document gets a title: never leave the value empty.
- `dates`: every date that says something about the life of the document, each with its role: `produced` (written, issued, signed), `received` (receipt stamps), `modified` (an excerpt or copy made later), `other`. Give `value` as YYYY-MM-DD, or YYYY-MM / YYYY when the day or month is missing, and always copy the date as written into `as_written`. Mark `uncertain` when the reading or the interpretation is doubtful (for example 3/4/37, which could be March or April). Dates merely mentioned in the body (meeting dates, birthdays) do not belong here. If the document has no date, return an empty list; do not guess one from the folder's date range.
- `place_origin`, `place_destination`: where it was created or sent from, and where it was sent to. Use the place name as written (Harbin, Peking, Tientsin).
- `creators`, `recipients`: names as written, with the role stated in the document (Secretary, District Governor). An organisation can be a creator or recipient. Empty list if not stated.
- `languages`: ISO 639-1 codes of all languages present.
- `summary`: 2-5 sentences in English on what the document is and says, with the concrete particulars a researcher would search for (names, amounts, decisions). Describe; do not evaluate.
- `has_illustration` and `illustrations`: photographs, drawings, cartoons, maps, with the page and a one-sentence alt text. Emblems, letterhead logos and stamps do not count.
- `has_table` and `tables`: true for tabular material (rosters in columns, attendance statistics, accounts). For each table give the page and a Markdown transcription that keeps rows and columns, cell text verbatim. For very long tables transcribe the header and the first 15 rows and say so in the caption.
- `items`: only when one sheet carries several separate items, such as two or more press clippings or pasted slips. Describe the sheet as a whole in the fields above (genre `news_clipping` for a sheet of clippings; the dates of all items in `dates` with type `produced`), then list each item here with its headline or a short description, its newspaper or source and date if shown, the page, and its first few words copied exactly. Leave empty for ordinary documents.
- `notes`: anything a reviewer should know: stamps and handwritten annotations, doubtful readings, damage, a suspected link to a neighbouring document.

Use only the document. The folder context line tells you which club's file this is, and nothing more; do not import facts from it or from your own knowledge into the record.
