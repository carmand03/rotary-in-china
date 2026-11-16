# Each target is one stage; `make all` runs them in order. Restrict with FILES="B37-F01 B10-F03".
PY ?= python3
F  := $(if $(FILES),--files $(FILES),)

inventory:  ; $(PY) -m rctb inventory
pages:      ; $(PY) -m rctb pages $(F)
transcribe: ; $(PY) -m rctb transcribe $(F)
segment:    ; $(PY) -m rctb segment $(F)
enrich:     ; $(PY) -m rctb enrich $(F)
build:      ; $(PY) -m rctb build
embed:      ; $(PY) -m rctb embed
all:        ; $(PY) -m rctb all $(F)

# Worked example on the sample folder, no language model needed
sample:
	$(PY) -m rctb inventory
	$(PY) -m rctb pages --files B37-F01
	$(PY) -m rctb load-gold gold/B37-F01.yaml
	$(PY) -m rctb build
	$(PY) -m rctb embed

app:    ; streamlit run app/app.py
test:   ; $(PY) -m pytest -q tests
schema: ; $(PY) -m rctb schema
.PHONY: inventory pages transcribe segment enrich build embed all sample app test schema
