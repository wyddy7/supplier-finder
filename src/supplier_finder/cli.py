"""Run the whole pipeline for categories x cities and write data/suppliers.json.

    uv run supplier-finder collect --city ekaterinburg --category packaging
    uv run supplier-finder collect --all
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from .categories import CATEGORIES, CITIES, Category
from .extract import DEFAULT_MODEL, Ledger, extract, is_empty_value, verify
from .schema import Supplier
from .score import questions_to_ask, score
from .site import read_site
from .twogis import TwoGis

log = logging.getLogger("supplier_finder")
OUT = Path("data/suppliers.json")
PER_QUERY_LIMIT = 15  # companies kept per category and city, after the relevance filter


async def collect(cat: Category, city: str, g: TwoGis, ledger: Ledger, sem: asyncio.Semaphore) -> list[Supplier]:
    found = {}
    for q in cat.queries:
        for d in await g.search(city, q, pages=2):
            if cat.rubrics & set(d.rubrics):  # relevance gate
                found.setdefault(d.org_id, d)
    log.info("%s / %s: %d relevant companies", cat.title, city, len(found))

    async def one(d) -> Supplier | None:
        async with sem:
            try:
                contacts = await asyncio.wait_for(g.contacts(d), timeout=90)
            except (RuntimeError, TimeoutError) as exc:
                log.warning("contacts failed for %s: %s", d.name, type(exc).__name__)
                return None
            s = Supplier(id=d.org_id, query=" / ".join(cat.queries), category=cat.key,
                         city=city, directory=d, contacts=contacts)
            if contacts.websites:
                try:  # a slow site must not hold the whole run
                    pages = await asyncio.wait_for(read_site(contacts.websites[0]), timeout=45)
                except TimeoutError:
                    log.info("site %s: gave up after 45 s", contacts.websites[0])
                    pages = []
                s.site_checked = [p.url for p in pages]
                raw = await extract(pages, d.name, cat.title, ledger)
                s.site, dropped = verify(raw, pages)
                if dropped:
                    log.info("%s: dropped unverified %s", d.name, dropped)
            score(s, set(cat.rubrics), cat.off_topic)
            return s

    out = [s for s in await asyncio.gather(*(one(d) for d in found.values())) if s]
    out.sort(key=lambda s: -s.score)
    return out[:PER_QUERY_LIMIT]


async def run(cats: list[Category], cities: list[str]) -> None:
    started = time.monotonic()
    ledger = Ledger()
    sem = asyncio.Semaphore(4)
    existing = json.loads(OUT.read_text()) if OUT.exists() else {"suppliers": [], "runs": []}
    keep = [s for s in existing["suppliers"]
            if not (s["category"] in {c.key for c in cats} and s["city"] in cities)]
    fresh: list[Supplier] = []
    async with TwoGis() as g:
        for city in cities:
            for cat in cats:
                fresh += await collect(cat, city, g, ledger, sem)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "categories": {c.key: c.title for c in CATEGORIES.values()},
        "cities": CITIES,
        "suppliers": keep + [s.model_dump() for s in fresh] ,
        "runs": existing.get("runs", []) + [{
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "categories": [c.key for c in cats],
            "cities": cities,
            "suppliers": len(fresh),
            "llm_model": os.environ.get("LLM_MODEL", DEFAULT_MODEL) if ledger.calls else None,
            "llm_calls": ledger.calls,
            "llm_usd": round(ledger.usd, 4),
            "llm_failures": ledger.failures,
            "seconds": round(time.monotonic() - started),
        }],
    }
    for s in payload["suppliers"]:
        s["ask"] = questions_to_ask(Supplier(**{k: v for k, v in s.items() if k != "ask"}))
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    log.info("wrote %d suppliers (%d new) in %ds, LLM %d calls $%.4f",
             len(payload["suppliers"]), len(fresh), time.monotonic() - started, ledger.calls, ledger.usd)


def rescore() -> None:
    """Re-apply the offline rules (empty-value filter, score, questions) to the saved snapshot."""
    data = json.loads(OUT.read_text())
    dropped = 0
    for raw in data["suppliers"]:
        raw.pop("ask", None)
        s = Supplier(**raw)
        for name, fact in s.site:
            if fact is not None and is_empty_value(fact.value):
                setattr(s.site, name, None); dropped += 1
        cat = CATEGORIES[s.category]
        score(s, set(cat.rubrics), cat.off_topic)
        raw.clear(); raw.update(s.model_dump(), ask=questions_to_ask(s))
    data["suppliers"].sort(key=lambda r: (r["city"], r["category"], -r["score"]))
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    log.info("rescored %d suppliers, dropped %d empty facts", len(data["suppliers"]), dropped)


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal .env reader: KEY=value lines, existing env wins."""
    if path.exists():
        for line in path.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> None:
    _load_dotenv()
    ap = argparse.ArgumentParser(prog="supplier-finder")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect", help="search, enrich, score and save")
    c.add_argument("--city", action="append", choices=list(CITIES))
    c.add_argument("--category", action="append", choices=list(CATEGORIES))
    c.add_argument("--all", action="store_true", help="every category in every city")
    sub.add_parser("rescore", help="re-apply offline rules to data/suppliers.json")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if args.cmd == "rescore":
        return rescore()
    cats = list(CATEGORIES.values()) if args.all or not args.category else [CATEGORIES[k] for k in args.category]
    cities = list(CITIES) if args.all or not args.city else args.city
    asyncio.run(run(cats, cities))
