"""Rotary in China Actor Database (RCAD).   python -m rcad <stage> [--config config.yaml] [--base actors]

  ingest    your workbooks and notes (actors/sources/)  ->  attestations (actors/work/)
  resolve   name variants -> persons / institutions; dictionaries     (actors/authority/, yours to edit)
  build     everything + links to the text database     ->  actors/out/rcad.sqlite and csv/
  all       the three in order
  web       articles harvested from rotaryinchina.org -> statements with their supporting passage (then run build)
  serve     print the command that opens the database in a browser
"""
import argparse
from pathlib import Path

from rctb.util import Config


def main() -> None:
    ap = argparse.ArgumentParser(prog="rcad", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("stage", choices=["ingest", "resolve", "build", "all", "web", "serve"])
    ap.add_argument("--limit", type=int, default=0, help="web: read only this many pages (trial)")
    ap.add_argument("--pages", nargs="*", help="web: only pages whose title or address contains one of these words")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--base", default="actors")
    a = ap.parse_args()
    cfg = Config(a.config)
    base = (cfg.base / a.base).resolve()
    from . import build, ingest, resolve
    if a.stage in ("ingest", "all"):
        ingest.run(base)
    if a.stage in ("resolve", "all"):
        resolve.run(base)
    if a.stage in ("build", "all"):
        build.run(base, cfg)
    if a.stage == "web":
        from . import web
        web.run(base, cfg, a.limit, a.pages)
    if a.stage == "serve":
        print(f"pip install datasette\ndatasette {base / 'out' / 'rcad.sqlite'} {cfg.out / 'rctb.sqlite'} -m {base / 'datasette.yaml'} --open")


if __name__ == "__main__":
    main()
