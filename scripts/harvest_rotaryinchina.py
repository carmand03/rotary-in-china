#!/usr/bin/env python3
"""Harvest https://rotaryinchina.org : every article and page, with the PDFs (and optionally images) they contain.

    python scripts/harvest_rotaryinchina.py --limit 5     # try on 5 pages first
    python scripts/harvest_rotaryinchina.py               # everything (about an hour)
    python scripts/harvest_rotaryinchina.py               # later: only what is new or changed

How it works (the site is a WordPress site):
  1. the list of pages is read from the site's own sitemap (sitemap_index.xml -> posts and pages),
     with the date each page was last modified;
  2. each page is downloaded once and kept as it was served (html/), and its title, dates, categories,
     tags and article text are extracted (text/ and manifest.csv);
  3. every PDF a page links to or shows in its PDF viewer is downloaded (files/), and with --images the
     images of the article too;
  4. links to other pages of the site that the sitemap does not list are followed as well.

Output (default actors/web/rotaryinchina/):
  manifest.csv   one row per page: url, title, dates, categories, tags, language hints, files, checksums, date fetched
  files.csv      one row per PDF / image: url, local path, size, checksum, the pages that use it
  html/  text/  files/        the pages as served, their article text, the PDFs and images (same paths as on the site)
  harvest.log    every request made, with its result

Good manners, built in: robots.txt is obeyed (the site excludes /wp-json/ and the PDF viewer script, neither is
used here), one request every --delay seconds, a User-Agent that says who you are, and nothing is fetched twice:
a second run only fetches pages whose sitemap date has changed. The site's footer reads "(c) Rotary Club of
Macau": the copy is for your own research until they have told you what may be reused.

Needs:  pip install requests beautifulsoup4
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib import robotparser
from urllib.parse import parse_qs, unquote, urljoin, urlsplit, urlunsplit, quote

import requests
from bs4 import BeautifulSoup

NS = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
FILE_EXT = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip")
IMG_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff")
SKIP_PATH = re.compile(r"/(wp-admin|wp-json|wp-login|wp-includes|feed|author|category|tag|page|comments)(/|$)|/wp-content/(plugins|themes)/", re.I)
SIZE_SUFFIX = re.compile(r"-\d{2,4}x\d{2,4}(?=\.\w+$)")       # WordPress thumbnails: photo-300x200.jpg -> photo.jpg
MANIFEST = ["url", "kind", "title", "published", "modified", "sitemap_lastmod", "categories", "tags", "scripts", "n_chars",
            "n_files", "files", "n_images", "html_path", "text_path", "html_sha256", "status", "fetched_at", "found_in"]
FILES = ["url", "kind", "local_path", "bytes", "sha256", "status", "fetched_at", "used_by"]


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canon(url: str, base: str) -> str:
    """Absolute URL without fragment; non-ASCII characters percent-encoded once, so one page has one spelling."""
    p = urlsplit(urljoin(base, url.strip()))
    return urlunsplit((p.scheme, p.netloc.lower(), quote(unquote(p.path), safe="/%:@+,;=~()!*'"), p.query, ""))


def local_path(url: str) -> Path:
    """Where a page or file of the site is stored: the same path as on the site, readable (decoded) and safe."""
    parts = [re.sub(r'[<>:"|?*\\\x00-\x1f]', "_", s)[:150] for s in unquote(urlsplit(url).path).split("/") if s and s not in (".", "..")]
    return Path(*parts) if parts else Path("index")


def scripts_of(text: str) -> str:
    """Rough language hint: which writing systems the article text uses."""
    cjk = len(re.findall(r"[一-鿿]", text))
    lat = len(re.findall(r"[A-Za-z]", text))
    tot = max(cjk + lat, 1)
    return "+".join(n for n, c in (("latin", lat), ("han", cjk)) if c / tot > 0.05) or "none"


class Harvest:
    def __init__(self, a):
        self.a, self.base, self.out = a, a.base.rstrip("/") + "/", Path(a.out)
        self.host = urlsplit(self.base).netloc.lower()
        for d in ("html", "text", "files"):
            (self.out / d).mkdir(parents=True, exist_ok=True)
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "rcad-harvest/1.0 (academic research on the history of Rotary in China" + (f"; contact: {a.contact})" if a.contact else ")")
        self.log = (self.out / "harvest.log").open("a", encoding="utf-8")
        self.last = 0.0
        self.rp = robotparser.RobotFileParser()
        r = self.get(urljoin(self.base, "robots.txt"), robots=False)
        self.rp.parse(r.text.splitlines() if r is not None and r.ok else [])
        self.pages = self.load("manifest.csv", MANIFEST)
        self.files = self.load("files.csv", FILES)

    # ---------------------------------------------------------------- plumbing
    def load(self, name, cols):
        p = self.out / name
        if not p.exists():
            return {}
        with p.open(encoding="utf-8-sig", newline="") as f:
            return {r["url"]: {c: r.get(c, "") for c in cols} for r in csv.DictReader(f)}

    def save(self):
        for name, cols, rows in (("manifest.csv", MANIFEST, self.pages), ("files.csv", FILES, self.files)):
            tmp = self.out / (name + ".tmp")
            with tmp.open("w", encoding="utf-8-sig", newline="") as f:
                w = csv.DictWriter(f, fieldnames=cols)
                w.writeheader()
                w.writerows(sorted(rows.values(), key=lambda r: r["url"]))
            tmp.replace(self.out / name)

    def note(self, *x):
        line = " | ".join(str(v) for v in x)
        self.log.write(f"{now()} | {line}\n")
        self.log.flush()

    def get(self, url, robots=True, stream=False):
        """One polite request: robots.txt, pause, three attempts. Returns the response, or None."""
        if robots and not self.rp.can_fetch(self.s.headers["User-Agent"], url):
            self.note("ROBOTS", url)
            return None
        for attempt in range(3):
            wait = self.a.delay - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            try:
                r = self.s.get(url, timeout=60, stream=stream)
                self.last = time.time()
                self.note(r.status_code, url)
                if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(int(r.headers.get("Retry-After", 0) or 0) or 30 * (attempt + 1))
                    continue
                return r
            except requests.RequestException as e:
                self.last = time.time()
                self.note("ERROR", url, repr(e)[:200])
                time.sleep(15 * (attempt + 1))
        return None

    def internal(self, url):
        return urlsplit(url).netloc.lower() in (self.host, "www." + self.host)

    # ---------------------------------------------------------------- 1. what is on the site
    def sitemap(self) -> dict[str, tuple[str, str]]:
        """{page url: (kind, lastmod)} from the sitemap index; category and tag listings are left out (they only repeat posts)."""
        todo, seen, found = [urljoin(self.base, "sitemap_index.xml")], set(), {}
        while todo:
            u = todo.pop(0)
            if u in seen:
                continue
            seen.add(u)
            r = self.get(u)
            if r is None or not r.ok:
                if u.endswith("sitemap_index.xml"):
                    todo.append(urljoin(self.base, "sitemap.xml"))
                continue
            root = ET.fromstring(r.content)
            for sm in root.findall("sm:sitemap/sm:loc", NS):
                if not re.search(r"(category|post_tag|author)-sitemap", sm.text or ""):
                    todo.append(sm.text.strip())
            kind = "page" if "page-sitemap" in u else "post"
            for n in root.findall("sm:url", NS):
                loc = n.findtext("sm:loc", "", NS).strip()
                if loc and self.internal(loc):
                    found[canon(loc, self.base)] = (kind, n.findtext("sm:lastmod", "", NS).strip())
        return found

    # ---------------------------------------------------------------- 2. one page
    def page(self, url, kind, lastmod, found_in):
        old = self.pages.get(url)
        if old and old["status"] == "200" and not self.a.refresh and (not lastmod or old["sitemap_lastmod"] == lastmod) \
                and (self.out / old["html_path"]).exists():
            html = (self.out / old["html_path"]).read_text(encoding="utf-8")       # unchanged: read our copy, ask the site nothing
            row = old
        else:
            r = self.get(url)
            row = dict.fromkeys(MANIFEST, "") | {"url": url, "kind": kind, "sitemap_lastmod": lastmod, "found_in": found_in,
                                                 "status": str(r.status_code) if r is not None else "failed", "fetched_at": now()}
            self.pages[url] = row
            if r is None or not r.ok or "html" not in r.headers.get("Content-Type", "html"):
                return []
            r.encoding = r.encoding if r.encoding and r.encoding.lower() != "iso-8859-1" else "utf-8"
            html = r.text
            rel = Path("html") / (local_path(url).as_posix() + ".html")
            (self.out / rel).parent.mkdir(parents=True, exist_ok=True)
            (self.out / rel).write_text(html, encoding="utf-8")
            row.update(html_path=rel.as_posix(), html_sha256=hashlib.sha256(html.encode()).hexdigest())
        soup = BeautifulSoup(html, "html.parser")
        meta = lambda *keys: next((m.get("content", "").strip() for k in keys for m in soup.find_all("meta", attrs={"property": k})
                                   + soup.find_all("meta", attrs={"name": k}) if m.get("content")), "")
        body = soup.select_one(".entry-content, .wp-block-post-content, article, main") or soup.body or soup
        links = {canon(t.get(k), url) for t in soup.find_all(True) for k in ("href", "src", "data-src", "data-pdf-url") if t.get(k)}
        # the PDF viewer plugin shows a file through viewer.php?file=<pdf>: take the PDF itself, never the viewer
        for u in list(links):
            if "viewer.php" in u or "file=" in u:
                links |= {canon(f, url) for f in parse_qs(urlsplit(u).query).get("file", [])}
        links |= {canon(m, url) for m in re.findall(r"https?://[^\s\"'<>\\]+?\.pdf", html, flags=re.I)}
        docs = sorted(u for u in links if unquote(urlsplit(u).path).lower().endswith(FILE_EXT) and self.internal(u))
        ext_docs = sorted(u for u in links if unquote(urlsplit(u).path).lower().endswith(FILE_EXT) and not self.internal(u))
        imgs = sorted({canon(SIZE_SUFFIX.sub("", t.get(k)), url) for t in body.find_all(["img", "a"]) for k in ("src", "href", "data-src")
                       if t.get(k) and unquote(urlsplit(t.get(k)).path).lower().endswith(IMG_EXT)}) if self.a.images else []
        imgs = [u for u in imgs if self.internal(u)]
        for t in body.select("script, style, noscript, nav, form, .sharedaddy, .jp-relatedposts"):
            t.decompose()
        text = re.sub(r"\n{3,}", "\n\n", body.get_text("\n", strip=True))
        title = (soup.find("h1").get_text(" ", strip=True) if soup.find("h1") else "") or meta("og:title") or (soup.title.string if soup.title else "")
        trel = Path("text") / (local_path(url).as_posix() + ".txt")
        (self.out / trel).parent.mkdir(parents=True, exist_ok=True)
        (self.out / trel).write_text(f"{title}\n{url}\n\n{text}\n", encoding="utf-8")
        tagged = lambda sel: "; ".join(dict.fromkeys(a.get_text(strip=True) for a in soup.select(sel) if a.get_text(strip=True)))
        row.update(title=title, published=meta("article:published_time"), modified=meta("article:modified_time", "og:updated_time"),
                   categories=tagged('a[rel~="category"], a[href*="/category/"]'), tags=tagged('a[href*="/tag/"]'),
                   scripts=scripts_of(text), n_chars=len(text), n_files=len(docs) + len(ext_docs), files="; ".join(docs + ext_docs),
                   n_images=len(imgs), text_path=trel.as_posix())
        for u, k in [(d, "document") for d in docs] + [(i, "image") for i in imgs]:
            self.file(u, k, url)
        # other pages of the site linked from this one (the sitemap may not list everything)
        return [u for u in links if self.internal(u) and not urlsplit(u).query and not SKIP_PATH.search(urlsplit(u).path)
                and not re.search(r"\.\w{2,5}$", urlsplit(u).path)]

    # ---------------------------------------------------------------- 3. one PDF or image
    def file(self, url, kind, used_by):
        row = self.files.setdefault(url, dict.fromkeys(FILES, "") | {"url": url, "kind": kind})
        row["used_by"] = "; ".join(dict.fromkeys([*filter(None, row["used_by"].split("; ")), used_by]))
        if row["status"] == "200" and (self.out / row["local_path"]).exists() and not self.a.refresh:
            return
        rel = Path("files") / local_path(url)
        (self.out / rel).parent.mkdir(parents=True, exist_ok=True)
        r = self.get(url, stream=True)
        row.update(status=str(r.status_code) if r is not None else "failed", fetched_at=now())
        if r is None or not r.ok:
            return
        h, n, tmp = hashlib.sha256(), 0, self.out / (rel.as_posix() + ".part")
        with tmp.open("wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
                h.update(chunk)
                n += len(chunk)
        tmp.replace(self.out / rel)
        row.update(local_path=rel.as_posix(), bytes=n, sha256=h.hexdigest())
        if kind == "document" and url.lower().endswith(".pdf") and not (self.out / rel).read_bytes()[:5].startswith(b"%PDF"):
            row["status"] = "200 but not a PDF"       # an error page served in place of the file

    # ---------------------------------------------------------------- run
    def run(self):
        listed = self.sitemap()
        print(f"sitemap: {len(listed)} pages listed ({sum(k == 'post' for k, _ in listed.values())} posts)")
        queue = [(u, k, m, "sitemap") for u, (k, m) in sorted(listed.items())]
        if not queue:
            queue = [(canon(self.base, self.base), "page", "", "start page")]
        done, n = set(), 0
        while queue and (not self.a.limit or n < self.a.limit):
            url, kind, lastmod, found_in = queue.pop(0)
            if url in done:
                continue
            done.add(url)
            n += 1
            try:
                more = self.page(url, kind, lastmod, found_in)
            except Exception as e:                     # one odd page must not stop the harvest
                self.note("PAGE ERROR", url, repr(e)[:300])
                self.pages.setdefault(url, dict.fromkeys(MANIFEST, "") | {"url": url})["status"] = f"error: {type(e).__name__}"
                more = []
            if not self.a.no_follow:
                queue += [(u, "linked", "", url) for u in more if u not in done and u not in listed]
            if n % 10 == 0:
                self.save()
                print(f"  {n} pages, {sum(f['status'] == '200' for f in self.files.values())} files; {len(queue)} to go", flush=True)
        self.save()
        ok = sum(p["status"] == "200" for p in self.pages.values())
        fok = [f for f in self.files.values() if f["status"] == "200"]
        bad = [p["url"] for p in self.pages.values() if p["status"] != "200"] + [f["url"] for f in self.files.values() if f["status"] != "200"]
        print(f"done: {ok} pages, {sum(f['kind'] == 'document' for f in fok)} documents, {sum(f['kind'] == 'image' for f in fok)} images, "
              f"{sum(int(f['bytes'] or 0) for f in fok) / 1e6:.0f} MB -> {self.out}")
        if bad:
            print(f"      {len(bad)} not retrieved (see the status column of manifest.csv / files.csv); run again to retry:")
            print("\n".join(f"        {u}" for u in bad[:10]))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--contact", default="", help="optional: an e-mail or web page written in the technical label of each request "
                    "(visible only in the server's logs; no message is sent to anyone)")
    ap.add_argument("--out", default="actors/web/rotaryinchina")
    ap.add_argument("--base", default="https://rotaryinchina.org/")
    ap.add_argument("--delay", type=float, default=3.0, help="seconds between two requests (default 3)")
    ap.add_argument("--limit", type=int, default=0, help="stop after this many pages (for a trial)")
    ap.add_argument("--images", action="store_true", help="also download the images of the articles (full size)")
    ap.add_argument("--no-follow", action="store_true", help="only the pages listed in the sitemap")
    ap.add_argument("--refresh", action="store_true", help="download everything again, even what has not changed")
    a = ap.parse_args()
    if a.delay < 1:
        sys.exit("--delay under 1 second is not polite to a small site.")
    Harvest(a).run()
