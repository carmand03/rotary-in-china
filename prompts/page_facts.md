You are describing one scanned page from the archives of Rotary clubs in China and Manchuria (1919-1952). The folders hold letters, membership rosters, semi-annual report forms, club bulletins, press clippings, certificates, binder cover sheets, tables of contents and the Rotary secretariat's record cards. Your description is used to work out where each document starts, so report only what you can see on this page; do not guess what came before or after it.

Look at the image first; the OCR text is a reading aid and may be wrong or out of order.

Fill in the fields in this order:

- `is_blank`: true only if the page carries no writing or printing at all (an empty reverse side or backing sheet).
- `page_kind`: what kind of page this is.
  - `letter`: a page of a letter, telegram or memo.
  - `form`: a printed form filled in by hand or typewriter (for example a semi-annual report of club membership).
  - `roster_or_list`: a list of members, officers, committees or addresses.
  - `bulletin`: a page of a club bulletin, newsletter or magazine.
  - `clipping`: one or more press cuttings, usually pasted on a sheet.
  - `certificate`: a charter, credential certificate, diploma.
  - `record_card`: an office index card or ledger card kept by the secretariat, including cards with pasted address slips.
  - `cover_or_contents`: a binder cover sheet, title page or table of contents.
  - `regulations`: constitution or by-laws.
  - `programme`: a programme, menu, invitation, brochure.
  - `photograph`: a page that is mainly a photograph or drawing.
  - `other`: none of these.
- `heading`: the title, masthead, letterhead name or form name printed or typed at the top of the page, copied as written and kept short (for example "TABLE OF CONTENTS", "THE HARBIN ROTARIAN No. 49", "Semi-Annual Report of Club Membership", "FOLLOW-UP WORK"). Null if the page has no heading of its own. A running head repeated on every page of a publication counts as a heading.
- `has_letterhead`: true if the top of the page carries a printed or typed letterhead (organisation name with address), as on the first page of a letter.
- `date_line`: a date standing alone near the top of the page, as written ("Harbin, July 3rd, 1936"). Null if there is none. Dates inside the running text do not count.
- `has_salutation`: true if the page has an opening address such as "Dear Sir", "Gentlemen", "Dear Rotarian Perry".
- `begins_mid_text`: true if the page clearly starts in the middle of something that began on an earlier page: a sentence without its beginning, a numbered list or table without its start or headings, a column of entries continuing.
- `printed_page_number`: the page number printed or typed on the page itself, as an integer. Null if the page shows none. Do not use the scan's page number.
- `looks_like_reverse_side`: true if this looks like the back of a form, card or certificate: printed instructions or by-laws in small type with nothing filled in, or an almost empty ruled side.
- `has_closing_signature`: true if the page ends with a complimentary close or signature block ("Yours sincerely", a signature, "Secretary").
