"""Categories a restaurant buys, and how each maps onto the directory.

``queries`` are what we type into 2GIS; ``rubrics`` decide whether a found
company is relevant at all (2GIS search is fuzzy: «упаковка» also returns
merch printers and flower shops).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Category:
    key: str
    title: str
    queries: tuple[str, ...]
    rubrics: frozenset[str]
    # Words in the company kind that mean «not for a restaurant» (merch printers, florists).
    off_topic: tuple[str, ...] = ()


CATEGORIES: dict[str, Category] = {
    c.key: c
    for c in (
        Category(
            "packaging",
            "Упаковка для доставки",
            ("одноразовая посуда оптом", "бумажная упаковка для еды"),
            frozenset({"Одноразовая посуда", "Бумажная упаковка"}),
            ("мерч", "сувенир", "флорист", "строительн", "типограф", "полиграф", "праздник"),
        ),
        Category(
            "vegetables",
            "Овощи и фрукты",
            ("овощи фрукты оптом",),
            frozenset({"Овощи и фрукты", "Ягоды и грибы"}),
        ),
        Category(
            "meat",
            "Мясо и птица",
            ("мясо оптом", "мясо птицы оптом"),
            frozenset({"Мясо и полуфабрикаты", "Мясо птицы и полуфабрикаты"}),
        ),
        Category(
            "fish",
            "Рыба и морепродукты",
            ("рыба морепродукты оптом",),
            frozenset({"Рыба и морепродукты"}),
        ),
        Category(
            "dairy",
            "Молочная продукция и сыры",
            ("молочная продукция оптом", "сыры оптом"),
            frozenset({"Молочные продукты", "Сыры"}),
        ),
    )
}

# 2GIS city slugs -> display names
CITIES = {
    "ekaterinburg": "Екатеринбург",
    "chelyabinsk": "Челябинск",
    "tyumen": "Тюмень",

}
