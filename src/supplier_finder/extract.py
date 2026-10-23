"""Turn a supplier's website pages into typed facts, then verify every fact.

The model is asked for a short value AND the exact sentence it took it from.
``verify`` drops any fact whose quote is not literally on the cited page, so a
hallucinated minimum order or certificate never reaches the user: it becomes
«неизвестно» instead.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field

import httpx
from pydantic import ValidationError

from .schema import Fact, SiteFacts
from .site import Page

log = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
# Picked from the live OpenRouter catalog on 2026-10-23: cheap, strong in
# Russian, supports structured outputs. Override with LLM_MODEL.
DEFAULT_MODEL = "anthropic/claude-haiku-5.5"
MAX_TOKENS = 3000  # the answer is a small JSON object; headroom for long quotes
BUDGET_USD = float(os.environ.get("LLM_BUDGET_USD", "1.0"))  # hard stop for one run

FIELDS = {
    "works_with_business": "работает ли с юрлицами / оптом / HoReCa, и как это сказано",
    "min_order": "минимальный заказ или минимальная партия (сумма, вес, штуки)",
    "price_hint": "любые цены или прайс: «от N ₽», цена за единицу, наличие прайс-листа",
    "delivery_terms": "условия доставки: бесплатно от суммы, сроки, самовывоз",
    "region_served": "куда возят: город, область, вся Россия",
    "certificates": "сертификаты, декларации соответствия, ветеринарные документы, ГОСТ, ТР ТС",
    "assortment": "что именно продают (коротко, 3–8 позиций)",
}

SYSTEM = (
    "Ты извлекаешь факты о поставщике для ресторана со страниц его сайта. "
    "Отвечай только тем, что прямо написано на страницах. Если факта нет — null "
    "(не пиши «не указано» и не описывай отсутствие). "
    "Для каждого найденного факта верни: value — коротко по-русски (до 120 символов); "
    "source_url — адрес страницы, где это написано (только из списка); "
    "quote — ДОСЛОВНЫЙ фрагмент текста этой страницы (15–200 символов), без изменений. "
    "Ответ — JSON."
)


def _schema() -> dict:
    fact = {
        "anyOf": [
            {"type": "null"},
            {
                "type": "object",
                "properties": {
                    "value": {"type": "string"},
                    "source_url": {"type": "string"},
                    "quote": {"type": "string"},
                },
                "required": ["value", "source_url", "quote"],
                "additionalProperties": False,
            },
        ]
    }
    return {
        "name": "supplier_facts",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {k: fact for k in FIELDS},
            "required": list(FIELDS),
            "additionalProperties": False,
        },
    }


@dataclass
class Ledger:
    """Running cost of the LLM stage, from OpenRouter's own usage.cost."""

    calls: int = 0
    usd: float = 0.0
    failures: list[str] = field(default_factory=list)


def _norm(s: str) -> str:
    s = s.lower().replace("ё", "е")
    s = re.sub(r"[«»\"“”„'`]", "", s)
    return re.sub(r"\s+", " ", s).strip()


# A value that only says the fact is missing is not a fact.
_EMPTY = re.compile(r"(не указан|не найден|нет информации|информации нет|не упомина|неизвестн)", re.I)


def is_empty_value(value: str) -> bool:
    return bool(_EMPTY.search(value))


def verify(facts: SiteFacts, pages: list[Page]) -> tuple[SiteFacts, list[str]]:
    """Keep a fact only if its quote is literally on the page it cites
    and its value states something (not «не указано»)."""
    by_url = {p.url: _norm(p.text) for p in pages}
    kept: dict[str, Fact | None] = {}
    dropped: list[str] = []
    for name in FIELDS:
        fact: Fact | None = getattr(facts, name)
        if fact is None:
            kept[name] = None
            continue
        page = by_url.get(fact.source_url)
        if page is not None and fact.quote and _norm(fact.quote) in page and not is_empty_value(fact.value):
            kept[name] = fact
        else:
            kept[name] = None
            dropped.append(name)
    return SiteFacts(**kept), dropped


async def extract(pages: list[Page], supplier: str, category: str, ledger: Ledger) -> SiteFacts:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key or not pages:
        return SiteFacts()
    if ledger.usd >= BUDGET_USD:
        log.warning("LLM budget $%.2f reached, %s left without site facts", BUDGET_USD, supplier)
        ledger.failures.append(supplier)
        return SiteFacts()
    model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    corpus = "\n\n".join(f"### {p.url}\n{p.text}" for p in pages)
    wanted = "\n".join(f"- {k}: {v}" for k, v in FIELDS.items())
    user = (
        f"Поставщик: {supplier}. Категория, которую ищет ресторан: {category}.\n"
        f"Поля:\n{wanted}\n\nСтраницы сайта:\n{corpus}"
    )
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
        "response_format": {"type": "json_schema", "json_schema": _schema()},
        "max_tokens": MAX_TOKENS,
        "temperature": 0,
        # Reasoning tokens are billed as output and can eat the cap before the JSON.
        "reasoning": {"enabled": False},
        "usage": {"include": True},
    }
    headers = {"Authorization": f"Bearer {key}", "X-Title": "supplier-finder"}
    for attempt in (1, 2, 3):
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                r = await client.post(OPENROUTER_URL, json=body, headers=headers)
            if r.status_code == 401:
                raise RuntimeError("OpenRouter 401: key expired or invalid")
            r.raise_for_status()
            data = r.json()
            ledger.calls += 1
            ledger.usd += float((data.get("usage") or {}).get("cost") or 0)
            choice = data["choices"][0]
            if choice.get("finish_reason") not in ("stop", "end_turn", None):
                raise ValueError(f"finish_reason={choice.get('finish_reason')}")
            raw = json.loads(choice["message"]["content"])
            return SiteFacts(**{k: (Fact(**v) if v else None) for k, v in raw.items() if k in FIELDS})
        except RuntimeError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, ValidationError) as exc:
            log.warning("extract %s attempt %d: %s %s", supplier, attempt, type(exc).__name__, str(exc)[:160])
    ledger.failures.append(supplier)
    return SiteFacts()
