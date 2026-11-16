"""Stage 1 - pages: every PDF page becomes an image plus up to three machine text layers.

  embedded  : the text layer already inside the PDF (pdftotext -layout)
  provided  : your existing per-page .txt files, if configured
  tesseract : a fresh OCR with the collection's languages (two passes: detect scripts, then re-read with the matching languages)

All layers are kept side by side in data/work/pages/<file_number>.jsonl together with cheap
quality indicators, so the choice of reference text is inspectable and reversible.
"""
from __future__ import annotations

import csv
import io
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from .util import Config, load_files, log_run, read_jsonl, script_shares, text_quality, tool_version, write_jsonl


def _tesseract(image: Path, langs: str, dpi: int) -> tuple[str, float | None]:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "o"
        subprocess.run(["tesseract", str(image), str(base), "-l", langs, "--psm", "3", "--dpi", str(dpi), "txt", "tsv"],
                       capture_output=True, check=True)
        text = base.with_suffix(".txt").read_text(encoding="utf-8").replace("\x0c", "").strip()
        confs = []
        for row in csv.DictReader(io.StringIO(base.with_suffix(".tsv").read_text(encoding="utf-8")),
                                  delimiter="\t", quoting=csv.QUOTE_NONE):
            try:
                c = float(row["conf"])
            except (KeyError, TypeError, ValueError):
                continue
            if c >= 0 and (row.get("text") or "").strip():
                confs.append(c)
        return text, (round(sum(confs) / len(confs), 1) if confs else None)


_CJK_GAP = re.compile(r"(?<=[\u3040-\u30ff\u3400-\u9fff]) (?=[\u3040-\u30ff\u3400-\u9fff])")


def choose_langs(shares: dict, ocr: dict) -> str:
    """Languages for the second OCR pass, from the script shares found in the first."""
    other = []
    if shares.get("kana", 0) >= ocr["kana_min"]:
        other.append("jpn")
    elif shares.get("cjk", 0) >= ocr["cjk_min"]:
        other.append("chi_tra")
    if shares.get("cyrillic", 0) >= ocr["cyrillic_min"]:
        other.append("rus")
    return "+".join(["eng"] + other if shares.get("latin", 1) >= 0.5 else other + ["eng"])


def _ocr_page(args) -> dict:
    pdf, page, ocr, fallback_image = args
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp) / "p"
        if pdf and shutil.which("pdftoppm"):
            subprocess.run(["pdftoppm", "-r", str(ocr["dpi"]), "-f", str(page), "-l", str(page), "-singlefile", "-png",
                            pdf, str(base)], capture_output=True, check=True)
            img = base.with_suffix(".png")
        else:
            img = Path(fallback_image)
        text, conf = _tesseract(img, ocr["langs_all"], ocr["dpi"])
        langs = choose_langs(script_shares(text), ocr)
        if langs != ocr["langs_all"]:
            text, conf = _tesseract(img, langs, ocr["dpi"])
    # Tesseract separates CJK characters with spaces; remove them so that 會員 can be searched as written.
    return {"page": page, "text_tesseract": _CJK_GAP.sub("", text), "tess_conf": conf, "tess_langs": langs}


def _existing_folder(cfg: Config, f: dict):
    ex = cfg["existing_pages"]
    cands = [cfg.base / ex["dir"] / t.format(stem=f["pdf_stem"], file_number=f["file_number"]) for t in ex["folder_patterns"]]
    folder = next((c for c in cands if c.is_dir()), None)
    if folder is None:
        print(f"pages: {f['file_number']} - no existing page folder (looked for {[c.name for c in cands]} in {ex['dir']})")
    return folder


def run(cfg: Config, only: list[str] | None = None, force: bool = False) -> None:
    ocr, ex = cfg["ocr"], cfg["existing_pages"]
    for f in load_files(cfg, only):
        fn, n = f["file_number"], f["n_pages"]
        pdf = str(cfg.base / f["pdf_path"]) if f.get("pdf_path") else None   # None: work from your PNG + text folder only
        out = cfg.work / "pages" / f"{fn}.jsonl"
        if out.exists() and not force and len(read_jsonl(out)) == n:
            # OCR is not repeated, but your text files are re-read so that they can be added or updated later.
            recs, changed = read_jsonl(out), 0
            folder = _existing_folder(cfg, f) if ex["enabled"] else None
            for rec in recs if folder else []:
                txt = next((folder / t.format(page=rec["page"]) for t in ex["txt_patterns"]
                            if (folder / t.format(page=rec["page"])).exists()), None)
                new = txt.read_text(encoding="utf-8", errors="replace").strip() if txt else None
                if new != rec.get("text_provided"):
                    rec["text_provided"], changed = new, changed + 1
                    if new is not None:
                        rec["quality_provided"] = text_quality(new)
            if changed:
                write_jsonl(out, recs)
            print(f"pages: {fn} up to date" + (f" ({changed} provided text files read)" if changed else ""))
            continue
        img_dir = cfg.out / "images" / fn
        img_dir.mkdir(parents=True, exist_ok=True)
        fmt = cfg["render"]["format"]
        have_poppler = bool(pdf and shutil.which("pdftoppm") and shutil.which("pdftotext"))
        if pdf and not have_poppler:
            from pypdf import PdfReader
            reader = PdfReader(pdf)
        recs = []
        folder = _existing_folder(cfg, f) if ex["enabled"] else None
        if folder is not None:
            n_txt = sum(1 for p in range(1, n + 1) if any((folder / t.format(page=p)).exists() for t in ex["txt_patterns"]))
            if n_txt != n:
                print(f"pages: {fn} - WARNING: PDF has {n} pages but {folder.name} has text for {n_txt}")
        for p in range(1, n + 1):
            rec = {"file_number": fn, "page": p, "text_provided": None}
            target = img_dir / f"p{p:04d}.{fmt}"
            fields = {"stem": f["pdf_stem"], "file_number": fn, "page": p}
            if folder is not None:
                txt = next((folder / t.format(**fields) for t in ex["txt_patterns"] if (folder / t.format(**fields)).exists()), None)
                src = next((folder / t.format(**fields) for t in ex["png_patterns"] if (folder / t.format(**fields)).exists()), None)
                if txt:
                    rec["text_provided"] = txt.read_text(encoding="utf-8", errors="replace").strip()
                if src and ex["use_png"]:
                    target = img_dir / f"p{p:04d}{src.suffix}"
                    if not target.exists():
                        shutil.copyfile(src, target)
                elif src and not have_poppler and not target.exists():
                    # no poppler on this machine: make the compact page image from your PNG instead of the PDF
                    from PIL import Image
                    im = Image.open(src).convert("RGB")
                    im.thumbnail((2400, 2400))
                    im.save(target, quality=88)
            if not target.exists():
                        shutil.copyfile(src, target)
            if not target.exists():
                if not have_poppler:
                    raise SystemExit(f"pages: {fn} p{p}: no page image. Without the PDF (or without poppler) every page "
                                     f"needs its PNG in your folder under {ex['dir']} (see config `existing_pages`).")
                subprocess.run(["pdftoppm", "-r", str(cfg["render"]["dpi"]), "-f", str(p), "-l", str(p), "-singlefile",
                                "-jpeg" if fmt == "jpg" else "-png", pdf, str(target.with_suffix(""))],
                               capture_output=True, check=True)
            rec["image"] = str(target.relative_to(cfg.out))
            if have_poppler:
                rec["text_embedded"] = subprocess.run(["pdftotext", "-f", str(p), "-l", str(p), "-layout", pdf, "-"],
                                                      capture_output=True, text=True).stdout.replace("\x0c", "").strip()
            else:
                rec["text_embedded"] = (reader.pages[p - 1].extract_text() or "").strip() if pdf else None
            recs.append(rec)
        if ocr["tesseract"] and not shutil.which("tesseract"):
            print(f"pages: {fn} - tesseract not installed here: that text layer is skipped")
        elif ocr["tesseract"]:
            with ProcessPoolExecutor(max_workers=ocr["workers"]) as pool:
                for r in pool.map(_ocr_page, [(pdf, p, ocr, str(cfg.out / recs[p - 1]["image"])) for p in range(1, n + 1)]):
                    recs[r["page"] - 1].update(r)
        for rec in recs:
            for layer in ("embedded", "provided", "tesseract"):
                t = rec.get(f"text_{layer}")
                if t is not None:
                    rec[f"quality_{layer}"] = text_quality(t)
            best = rec.get("text_provided") or rec.get("text_tesseract") or rec.get("text_embedded") or ""
            rec["scripts"] = script_shares(best)
            rec["blank_candidate"] = all(len((rec.get(f"text_{l}") or "").strip()) < 15
                                         for l in ("embedded", "provided", "tesseract"))
        write_jsonl(out, recs)
        print(f"pages: {fn} - {n} pages")
    log_run(cfg, "pages", files=only or "all", tesseract=tool_version(["tesseract", "--version"]),
            poppler=tool_version(["pdftoppm", "-v"]), ocr=ocr)
