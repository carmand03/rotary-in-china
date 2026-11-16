# Inputs

- `rotary_index.xlsx`: the index of the 63 archive folders (club, volume, dates). Included.
- `pdf/`: put the scanned folders here as PDF, named as in the index (`AC0033-B37-F01.pdf`). Not included.
- `pages/`: or put per-folder page folders here, `AC0033-B37-F01_folder/image_0001.png` with the
  matching OCR text `image_0001.text`. Not included.

Either form is enough for `python -m rctb inventory` and `python -m rctb pages --files B37-F01`.
The sample edition ships the results of those steps for `B37-F01` (`data/work/`, `data/out/`), so
the databases and viewers work without the scans.
