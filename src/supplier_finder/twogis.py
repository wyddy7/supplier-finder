"""Discovery and contacts from 2GIS, the main business directory in Russian cities.

2GIS renders its pages from a JSON state embedded in the HTML. We open the
public search page and the public firm page in a headless browser and read
that state, so we get the same data a person sees, with a link back to it.

A production version should use the official 2GIS Catalog API (paid key);
the record shape stays the same.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from urllib.parse import quote, unquote

from playwright.async_api import Browser, Page, async_playwright

from .schema import Contacts, Directory

log = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
BASE = "https://2gis.ru"


def _browser_path() -> str | None:
    """Use a locally cached headless shell if there is one (PW_CHROME overrides)."""
    if env := os.environ.get("PW_CHROME"):
        return env
    cache = Path.home() / "Library/Caches/ms-playwright"
    found = sorted(cache.glob("chromium_headless_shell-*/chrome-headless-shell-*/chrome-headless-shell"))
    return str(found[-1]) if found else None


_STATE_JS = "() => (typeof initialState !== 'undefined' ? initialState : null)"


class TwoGis:
    def __init__(self) -> None:
        self._pw = None
        self._browser: Browser | None = None

    async def __aenter__(self) -> TwoGis:
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(executable_path=_browser_path())
        # One context for the whole run: cookies survive, so the silent sign-in
        # redirect 2GIS does on the first visit happens only once.
        self._ctx = await self._browser.new_context(locale="ru-RU", user_agent=UA)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    @staticmethod
    async def _wait_state(page: Page, timeout_s: float = 15.0) -> dict | None:
        """The state appears after a redirect or two; poll for it."""
        for _ in range(int(timeout_s / 0.5)):
            try:
                state = await page.evaluate(_STATE_JS)
            except Exception:  # noqa: BLE001 - navigation in progress
                state = None
            if state:
                return state
            # 2GIS sometimes shows an «update your browser» page first.
            skip = page.get_by_text("Пропустить обновление")
            try:
                if await skip.count():
                    await skip.first.click()
            except Exception:  # noqa: BLE001 - the page navigated under us
                pass
            await asyncio.sleep(0.5)
        return None

    async def _state(self, url: str, attempts: int = 3) -> dict:
        """Open a page and return its embedded state. Retries on timeouts."""
        assert self._browser
        last: Exception | None = None
        for attempt in range(1, attempts + 1):
            page: Page = await self._ctx.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                state = await self._wait_state(page)
                if state:
                    return state
                raise RuntimeError("page has no embedded state")
            except Exception as exc:  # noqa: BLE001 - retried, then re-raised
                last = exc
                log.warning("2gis attempt %d/%d failed: %s: %s", attempt, attempts, type(exc).__name__, url)
                await asyncio.sleep(2 * attempt)
            finally:
                await page.close()
        raise RuntimeError(f"2gis unreachable after {attempts} attempts: {url}") from last

    async def search(self, city: str, query: str, pages: int = 2) -> list[Directory]:
        """Firms for a query in a city, one entry per organisation (branches folded)."""
        seen: dict[str, Directory] = {}
        for n in range(1, pages + 1):
            url = f"{BASE}/{city}/search/{quote(query)}" + (f"/page/{n}" if n > 1 else "")
            state = await self._state(url)
            profiles = state["data"]["entity"]["profile"]
            if not profiles:
                break
            for firm_id, wrapped in profiles.items():
                d = wrapped["data"]
                if d.get("type") != "branch":
                    continue
                org = d.get("org") or {}
                org_id = str(org.get("id") or firm_id)
                if org_id in seen:
                    continue
                seen[org_id] = _directory(firm_id, d, city)
        return list(seen.values())

    async def contacts(self, d: Directory) -> Contacts:
        state = await self._state(d.url)
        data = state["data"]["entity"]["profile"][d.firm_id]["data"]
        return _contacts(data, d.url)


def _directory(firm_id: str, d: dict, city: str) -> Directory:
    org = d.get("org") or {}
    reviews = d.get("reviews") or {}
    rubrics = [r["name"] for r in d.get("rubrics", [])]
    kind = org.get("extension") or (d.get("name_ex") or {}).get("extension")
    attrs = {
        a["name"].lower()
        for g in d.get("attribute_groups", [])
        for a in g.get("attributes", [])
    }
    text = " ".join([kind or "", *rubrics]).lower()
    return Directory(
        firm_id=firm_id,
        org_id=str(org.get("id") or firm_id),
        name=org.get("primary") or d["name"].split(",")[0],
        kind=kind,
        address=d.get("address_name"),
        branch_count=int(org.get("branch_count") or 1),
        rubrics=rubrics,
        rating=reviews.get("org_rating") or reviews.get("general_rating"),
        review_count=int(reviews.get("org_review_count") or reviews.get("general_review_count") or 0),
        wholesale=("опт" in text) or None,
        production=("производ" in text) or None,
        delivery=("доставка" in attrs) or None,
        url=f"{BASE}/{city}/firm/{firm_id}",
    )


_NOT_SITES = ("jivo.chat", "jivosite", "taplink", "rutube.ru", "youtube.com", "instagram.com", "max.ru", "t.me", "vk.com", "wa.me", "ok.ru")
_MESSENGERS = {"telegram", "whatsapp", "vkontakte", "viber", "max"}


def _real_url(c: dict) -> str:
    """2GIS wraps outbound links in a tracker; the real URL is after the '?'."""
    value = c.get("url") or c.get("value") or ""
    if "link.2gis.ru" in value and "?" in value:
        return unquote(value.split("?", 1)[1])
    return value


def _contacts(data: dict, source_url: str) -> Contacts:
    out = Contacts(source_url=source_url)
    for group in data.get("contact_groups") or []:
        for c in group.get("contacts", []):
            kind = c.get("type")
            if kind == "phone":
                out.phones.append(c.get("print_text") or c.get("text") or c["value"])
            elif kind == "email":
                out.emails.append(c["value"])
            elif kind == "website":
                url = _real_url(c)
                if not any(s in url for s in _NOT_SITES):
                    out.websites.append(url)
            elif kind in _MESSENGERS:
                out.messengers.setdefault(kind, _real_url(c))
    out.phones = list(dict.fromkeys(out.phones))
    out.emails = list(dict.fromkeys(out.emails))
    out.websites = list(dict.fromkeys(out.websites))
    return out


def dump_state(state: dict, path: Path) -> None:
    """Debug helper: save a page state to look at its shape."""
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1))
