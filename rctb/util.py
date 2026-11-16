"""Shared helpers: configuration, JSONL I/O, hashing, run log, script detection."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import threading
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


class Config(dict):
    """config.yaml as a dict, with resolved paths."""

    def __init__(self, path: str | Path = "config.yaml"):
        self.path = Path(path).resolve()
        self.base = self.path.parent
        raw = self.path.read_text(encoding="utf-8")
        super().__init__(yaml.safe_load(raw))
        self.sha = hashlib.sha256(raw.encode()).hexdigest()[:12]

    def p(self, key: str) -> Path:
        return (self.base / self["paths"][key]).resolve()

    @property
    def work(self) -> Path:
        return self.p("work_dir")

    @property
    def out(self) -> Path:
        return self.p("out_dir")


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _atomic_write(path: Path, text: str) -> None:
    """Write to a temporary file, then rename: a job killed mid-write never leaves a half-written file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_jsonl(path: Path, rows) -> None:
    _atomic_write(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def read_json(path: Path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:  # damaged file from an interrupted run: treat as missing, it will be redone
        return default


def write_json(path: Path, obj) -> None:
    _atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=2))


def tool_version(cmd: list[str]) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
        return (r.stdout or r.stderr).strip().splitlines()[0]
    except Exception:
        return "not found"


def log_run(cfg: Config, stage: str, **info) -> None:
    """Append one line per stage run to data/work/runs.jsonl (who/what/when/with which settings)."""
    git = tool_version(["git", "-C", str(cfg.base), "rev-parse", "--short", "HEAD"])
    rec = {
        "stage": stage,
        "time": now(),
        "pipeline_version": cfg["project"]["pipeline_version"],
        "config_sha": cfg.sha,
        "git_commit": git if re.fullmatch(r"[0-9a-f]{7,}", git) else None,
        **info,
    }
    cfg.work.mkdir(parents=True, exist_ok=True)
    with open(cfg.work / "runs.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def load_files(cfg: Config, only: list[str] | None = None, need_pdf: bool = True) -> list[dict]:
    files = read_jsonl(cfg.work / "files.jsonl")
    if not files:
        raise SystemExit("No inventory yet: run `python -m rctb inventory` first.")
    if only:
        files = [f for f in files if f["file_number"] in only]
    if need_pdf:
        files = [f for f in files if f.get("available", f.get("pdf_present"))]  # a PDF or a folder of page images
    return files


# ---------------------------------------------------------------- text helpers
_RANGES = {
    "latin": [(0x41, 0x5A), (0x61, 0x7A), (0xC0, 0x24F)],
    "cjk": [(0x4E00, 0x9FFF), (0x3400, 0x4DBF)],
    "kana": [(0x3040, 0x30FF)],
    "cyrillic": [(0x400, 0x4FF)],
    "hangul": [(0xAC00, 0xD7AF)],
}


def script_shares(text: str) -> dict[str, float]:
    """Share of letters belonging to each script (0-1). Empty dict if no letters."""
    counts = dict.fromkeys(_RANGES, 0)
    for ch in text:
        o = ord(ch)
        for name, ranges in _RANGES.items():
            if any(a <= o <= b for a, b in ranges):
                counts[name] += 1
                break
    total = sum(counts.values())
    return {k: round(v / total, 3) for k, v in counts.items() if v} if total else {}


def text_quality(text: str) -> dict:
    """Cheap, model-free indicators of OCR trouble.

    single_char_ratio: share of 1-letter Latin tokens (high = letter-spaced typescript mis-read
    as "H a r b i n"); odd_token_ratio: share of tokens mixing letters with symbols/digits.
    """
    tokens = text.split()
    latin = [t for t in tokens if re.search(r"[A-Za-z]", t)]
    if not latin:
        return {"n_chars": len(text.strip()), "n_tokens": len(tokens), "single_char_ratio": None, "odd_token_ratio": None}
    single = sum(1 for t in latin if len(t.strip(".,;:()'\"")) == 1)
    odd = sum(1 for t in latin if not re.fullmatch(r"[\(\"'“‘]*[A-Za-z][A-Za-z'’\-]*[\.,;:!\?\)\"'”’]*", t))
    return {
        "n_chars": len(text.strip()),
        "n_tokens": len(tokens),
        "single_char_ratio": round(single / len(latin), 3),
        "odd_token_ratio": round(odd / len(latin), 3),
    }


def norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def doc_id_for(file_number: str, start_page: int) -> str:
    """Stable identifier: file number + first page, e.g. B37-F01-p008."""
    return f"{file_number}-p{start_page:03d}"
