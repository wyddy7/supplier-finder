"""Rank suppliers for «whom to contact first» with a score you can read.

No black box: the score is a sum of named parts, and every part is shown
in the UI next to the supplier.
"""

from __future__ import annotations

import math

from .schema import ScoreReason, Supplier

B2B_WORDS = ("опт", "производ", "торговый дом", "дистриб", "поставщик", "склад", "логистич", "торгово")
RETAIL_WORDS = ("магазин", "лавка", "супермаркет", "гипермаркет", "кофейня", "кафе")

ASK_LABELS = {
    "min_order": "минимальный заказ",
    "price_hint": "цены / прайс",
    "delivery_terms": "условия доставки",
    "certificates": "документы и сертификаты",
    "region_served": "регион доставки",
}


def score(s: Supplier, primary_rubrics: set[str], off_topic: tuple[str, ...] = ()) -> None:
    reasons: list[ScoreReason] = []
    d = s.directory
    kind = (d.kind or "").lower()

    # 1. Works with businesses (max 30)
    if any(w in kind for w in B2B_WORDS):
        reasons.append(ScoreReason(label=f"B2B-профиль: «{d.kind}»", points=20))
    elif any(w in kind for w in RETAIL_WORDS):
        reasons.append(ScoreReason(label=f"розничная точка: «{d.kind}»", points=0))
    if s.site.works_with_business:
        reasons.append(ScoreReason(label="на сайте: работа с юрлицами / оптом", points=10))

    # 2. Relevance to the category (max 20)
    if d.rubrics and d.rubrics[0] in primary_rubrics:
        reasons.append(ScoreReason(label=f"основная рубрика: {d.rubrics[0]}", points=20))
    elif primary_rubrics & set(d.rubrics):
        reasons.append(ScoreReason(label="категория среди рубрик компании", points=10))

    # 3. Can we reach them quickly (max 15)
    c = s.contacts
    reach = 5 * bool(c.phones) + 4 * bool(c.emails) + 3 * bool(c.websites) + 3 * bool(c.messengers)
    if reach:
        reasons.append(ScoreReason(label="есть контакты: " + ", ".join(
            n for n, ok in (("телефон", c.phones), ("почта", c.emails), ("сайт", c.websites), ("мессенджер", c.messengers)) if ok
        ), points=reach))

    # 4. Terms known before the first call (max 15)
    known = [label for f, label in ASK_LABELS.items() if getattr(s.site, f)]
    if known:
        reasons.append(ScoreReason(label="условия на сайте: " + ", ".join(known), points=3 * len(known)))

    # 5. Reputation, discounted when there are few reviews (max 20)
    if d.rating and d.review_count:
        confidence = min(1.0, math.log10(d.review_count + 1) / 2)  # 100+ reviews = full weight
        pts = round(d.rating / 5 * confidence * 20, 1)
        reasons.append(ScoreReason(label=f"рейтинг {d.rating} по {d.review_count} отзывам", points=pts))
    else:
        reasons.append(ScoreReason(label="нет отзывов в 2ГИС", points=0))

    # 6. Probably not a food supplier at all: the company kind or one of its
    # main rubrics points elsewhere (merch printing, florists, building stores)
    rubric_hit = next((r for r in d.rubrics[:3] for w in off_topic if w in r.lower()), None)
    if any(w in kind for w in off_topic):
        reasons.append(ScoreReason(label=f"скорее не для общепита: «{d.kind}»", points=-30))
    elif rubric_hit:
        reasons.append(ScoreReason(label=f"скорее не для общепита: рубрика «{rubric_hit}»", points=-30))

    s.reasons = reasons
    s.score = round(sum(r.points for r in reasons), 1)


def questions_to_ask(s: Supplier) -> list[str]:
    """What the buyer still has to find out on the first call."""
    return [label for f, label in ASK_LABELS.items() if not getattr(s.site, f)]
