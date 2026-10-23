"""Offline checks of the two rules the whole service relies on:
a fact without a literal quote never reaches the user, and the score is
explainable (it equals the sum of its reasons)."""

from supplier_finder.extract import verify
from supplier_finder.schema import Contacts, Directory, Fact, SiteFacts, Supplier
from supplier_finder.score import questions_to_ask, score
from supplier_finder.site import Page

PAGE = Page(
    url="https://example.ru/dostavka",
    text="Доставка по  Екатеринбургу бесплатно при заказе от 15 000 ₽. Работаем с юридическими лицами.",
)


def test_verify_keeps_literal_quote_and_drops_invented_one():
    facts = SiteFacts(
        delivery_terms=Fact(value="бесплатно от 15 000 ₽", source_url=PAGE.url,
                            quote="Доставка по Екатеринбургу бесплатно при заказе от 15 000 ₽"),
        certificates=Fact(value="есть декларации", source_url=PAGE.url,
                          quote="Вся продукция сертифицирована"),
    )
    kept, dropped = verify(facts, [PAGE])
    assert kept.delivery_terms is not None
    assert kept.certificates is None
    assert dropped == ["certificates"]


def test_verify_drops_fact_citing_a_page_we_did_not_read():
    facts = SiteFacts(min_order=Fact(value="от 5 кг", source_url="https://example.ru/other", quote="Доставка"))
    kept, dropped = verify(facts, [PAGE])
    assert kept.min_order is None and dropped == ["min_order"]


def _supplier(kind: str, rubrics: list[str], rating=4.8, reviews=120) -> Supplier:
    d = Directory(firm_id="1", org_id="1", name="Тест", kind=kind, rubrics=rubrics,
                  rating=rating, review_count=reviews, url="https://2gis.ru/x")
    c = Contacts(phones=["+7 343 000-00-00"], websites=["https://example.ru"], source_url=d.url)
    return Supplier(id="1", query="q", category="packaging", city="ekaterinburg", directory=d, contacts=c)


def test_score_is_sum_of_reasons_and_wholesale_beats_retail():
    wholesale = _supplier("оптовая компания", ["Одноразовая посуда"])
    retail = _supplier("магазин упаковки", ["Одноразовая посуда"])
    for s in (wholesale, retail):
        score(s, {"Одноразовая посуда"})
        assert s.score == round(sum(r.points for r in s.reasons), 1)
    assert wholesale.score > retail.score


def test_off_topic_company_is_pushed_down():
    merch = _supplier("компания по производству мерча и упаковки", ["Бумажная упаковка"])
    food = _supplier("производственная компания", ["Бумажная упаковка"])
    score(merch, {"Бумажная упаковка"}, ("мерч",))
    score(food, {"Бумажная упаковка"}, ("мерч",))
    assert food.score - merch.score >= 30


def test_unknown_terms_become_questions_for_the_first_call():
    s = _supplier("оптовая компания", ["Одноразовая посуда"])
    s.site = SiteFacts(min_order=Fact(value="от 5 000 ₽", source_url=PAGE.url, quote="x"))
    assert "минимальный заказ" not in questions_to_ask(s)
    assert "документы и сертификаты" in questions_to_ask(s)


def test_verify_drops_value_that_only_says_missing():
    facts = SiteFacts(certificates=Fact(value="сертификаты не указаны", source_url=PAGE.url,
                                        quote="Работаем с юридическими лицами"))
    kept, dropped = verify(facts, [PAGE])
    assert kept.certificates is None and dropped == ["certificates"]
