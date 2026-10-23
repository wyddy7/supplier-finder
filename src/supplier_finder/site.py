"""Read a supplier's own website: the home page plus the few pages that
usually hold B2B terms (delivery, wholesale, contacts, certificates)."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from selectolax.lexbor import LexborHTMLParser as HTMLParser

log = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)

# Link text or URL fragments of pages worth reading, in priority order.
_INTERESTING = [
    ("dostav", "достав"),
    ("opt", "опт"),
    ("partner", "партн"),
    ("horeca", "horeca"),
    ("sertif", "сертиф"),
    ("dokument", "документ"),
    ("usloviya", "услови"),
    ("price", "прайс"),
    ("o-kompanii", "о компании"),
    ("about", "о нас"),
    ("contact", "контакт"),
]
MAX_PAGES = 4
MAX_CHARS = 12_000  # per page, after cleanup


@dataclass
class Page:
    url: str
    text: str


def _clean_text(html: str) -> tuple[str, HTMLParser]:
    tree = HTMLParser(html)
    for tag in ("script", "style", "noscript", "svg", "iframe"):
        for node in tree.css(tag):
            node.decompose()
    body = tree.body or tree.root
    text = body.text(separator=" ") if body else ""
    text = re.sub(r"\s+", " ", text).strip()
    return text[:MAX_CHARS], tree


def _pick_links(tree: HTMLParser, base: str) -> list[str]:
    host = urlparse(base).netloc.removeprefix("www.")
    scored: list[tuple[int, str]] = []
    for a in tree.css("a[href]"):
        href = a.attributes.get("href") or ""
        url = urljoin(base, href).split("#")[0]
        if urlparse(url).netloc.removeprefix("www.") != host or url.rstrip("/") == base.rstrip("/"):
            continue
        if re.search(r"\.(pdf|vcf|jpe?g|png|webp|docx?|xlsx?|zip|rar)$", urlparse(url).path, re.I):
            continue
        label = (a.text() or "").lower()
        low = url.lower()
        for rank, (slug, word) in enumerate(_INTERESTING):
            if slug in low or word in label:
                scored.append((rank, url))
                break
    seen: list[str] = []
    for _, url in sorted(scored):
        if url not in seen:
            seen.append(url)
    return seen[: MAX_PAGES - 1]


async def _get(client: httpx.AsyncClient, url: str) -> tuple[str, str] | None:
    """(final url after redirects, html) or None."""
    for attempt in (1, 2):
        try:
            r = await client.get(url)
            if r.status_code == 200 and "html" in r.headers.get("content-type", ""):
                return str(r.url), r.text
            log.info("site %s -> HTTP %s", url, r.status_code)
            return None
        except httpx.HTTPError as exc:
            log.info("site %s attempt %d: %s", url, attempt, type(exc).__name__)
            await asyncio.sleep(1)
    return None


async def read_site(url: str) -> list[Page]:
    """Home page + up to three relevant inner pages. Empty list if unreachable."""
    if not url.startswith("http"):
        url = "http://" + url
    async with httpx.AsyncClient(
        headers={"User-Agent": UA, "Accept-Language": "ru-RU,ru;q=0.9"},
        follow_redirects=True,
        timeout=httpx.Timeout(15.0),
        verify=False,  # many small supplier sites have broken TLS chains
    ) as client:
        got = await _get(client, url)
        if not got:
            return []
        url, html = got
        text, tree = _clean_text(html)
        pages = [Page(url, text)]
        for link in _pick_links(tree, url):
            sub = await _get(client, link)
            if sub and sub[0] not in {p.url for p in pages}:
                pages.append(Page(sub[0], _clean_text(sub[1])[0]))
        return pages
