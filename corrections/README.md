# Human corrections

Files here override machine output at `build` time and are never touched by the pipeline.
Keep this folder under version control: it is the scholarly work.

## Page text: `pages/<file_number>/p0027.txt`
The complete corrected transcription of one page, as plain UTF-8 text. It becomes the page's
reference text (layer `corrected`).

## Document boundaries: `segments/<file_number>.yaml`
Only list the pages whose label you change.

```yaml
pages:
  - {page: 34, relation: continues_earlier, continues_page: 32}   # reverse of the card on p. 32
  - {page: 9,  relation: continues}                               # treat letter + form as one document
  - {page: 37, relation: blank}
```
Relations: `new`, `continues` (same document as the preceding non-blank page),
`continues_earlier` (give `continues_page`), `blank`.

## Document fields: `documents.yaml`
Keyed by `doc_id`; give only the fields you change, in the same structure as the record.

```yaml
B37-F01-p027:
  creators: [{name: Yoshio Gundsi, role: Club Secretary, kind: person}]
  dates:
    - {type: produced, value: "1937-06-10", as_written: "10/VI, 1937", certainty: certain}
B37-F01-p045:
  genre: pamphlet
```
