You are helping to build a text database from the archives of Rotary clubs in China and Manchuria (1919-1952). Each PDF is one archival folder: a stack of separate documents scanned page after page (letters, membership rosters, semi-annual report forms, club bulletins, press clippings, certificates, office record cards). The database stores one record per document, so we need to know where each document starts and ends.

You will see a run of consecutive pages from one folder, as images, with a rough OCR excerpt of each. Label every page with its relation to the pages before it:

- `new`: the page begins a document.
- `continues`: the page belongs to the same document as the nearest preceding non-blank page (next page of a letter, next page of a bulletin issue, the reverse side of a form or card).
- `continues_earlier`: the page belongs to a document that started earlier but was interrupted by other material; give the page it follows in `continues_page`.
- `blank`: no content at all (empty reverse sides, empty backing sheets).

What makes a document: it was produced as one item, by one creator, at one time. Rules for this collection:

- A binder cover sheet, a table of contents, a roster, a form, a letter, a bulletin issue, a certificate and a record card are each their own document, even when they follow one another and concern the same club.
- A cover letter and the form it accompanies are two documents: a page with a letterhead, a date line and a salutation starts a new document even right after a form.
- Each issue of a bulletin is one document, covering all of its pages. A multi-page roster is one document.
- The reverse side of a form, card or certificate continues it; a different card, even of the same series, is a new document.
- A sheet onto which several small slips or clippings were pasted is one document.

Evidence that a page continues the previous one: running page numbers, a sentence carried over the page break, the same layout, typeface and paper, a signature block closing a text that began earlier. Evidence of a new document: a letterhead, a date line, a salutation, a title or masthead, a different form, a change of paper colour or size. Colour and size are only visible in the images, so look at them and do not rely on the OCR alone.

The first page you are shown may itself be a continuation of something you cannot see; label it by your best judgement, it will be reconciled with the preceding run of pages afterwards.

Return one label for every page you were shown, using the page numbers given. For each page, first write `reason`: one short sentence naming what on the page shows it (for example "letterhead and date line 8 July 1937", "page number 3, sentence continues", "reverse of the certificate, printed by-laws"). Then give `relation`. Use `note` only for something a human reviewer should double-check.
