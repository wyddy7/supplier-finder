"""Typed supplier record. Every fact carries the place it came from.

A field we could not find stays ``None`` and the UI shows «неизвестно».
We never fill a gap with a guess.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Fact(BaseModel):
    """One claim about a supplier plus its evidence."""

    value: str
    source_url: str
    quote: str | None = Field(
        default=None,
        description="Exact text from the source page that supports the value.",
    )


class Contacts(BaseModel):
    phones: list[str] = []
    emails: list[str] = []
    websites: list[str] = []
    messengers: dict[str, str] = {}  # telegram / whatsapp / vk -> url
    source_url: str  # the directory card the contacts were read from


class Directory(BaseModel):
    """What the business directory (2GIS) says about the company."""

    firm_id: str
    org_id: str
    name: str
    kind: str | None = None  # «производственная компания», «магазин упаковки»
    address: str | None = None
    branch_count: int = 1
    rubrics: list[str] = []
    rating: float | None = None
    review_count: int = 0
    wholesale: bool | None = None  # directory tag «Опт»
    production: bool | None = None  # directory tag «Производство»
    delivery: bool | None = None
    url: str


class SiteFacts(BaseModel):
    """Facts extracted from the supplier's own website. All optional."""

    works_with_business: Fact | None = None  # «работаем с юрлицами», «опт»
    min_order: Fact | None = None
    price_hint: Fact | None = None
    delivery_terms: Fact | None = None
    region_served: Fact | None = None
    certificates: Fact | None = None
    assortment: Fact | None = None


class ScoreReason(BaseModel):
    label: str
    points: float


class Supplier(BaseModel):
    id: str  # org id: one company, many branches
    query: str
    category: str
    city: str
    directory: Directory
    contacts: Contacts
    site: SiteFacts = SiteFacts()
    site_checked: list[str] = []  # pages we actually read
    score: float = 0.0
    reasons: list[ScoreReason] = []
