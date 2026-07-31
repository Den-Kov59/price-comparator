"""Offline: parse the saved Phase 0 fixtures. No network, no pytest.

    python tests/test_adapters.py
"""

import json
import pathlib
import sys
from decimal import Decimal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from price_comparator import db, models  # noqa: E402
from price_comparator.adapters import (  # noqa: E402
    _ARTICLE, _atb_product, _silpo_product, _zakaz_product)

FIX = pathlib.Path(__file__).parent / "fixtures"
load = lambda n: json.loads((FIX / n).read_text(encoding="utf-8"))


def test_zakaz():
    raw = load("zakaz_metro_pringles.json")["results"]
    ps = [_zakaz_product("metro", it) for it in raw]
    assert len(ps) == len(raw)
    p = next(p for p in ps if "Pringles Паприка" in p.name)
    assert p.price == Decimal("174.90"), p.price      # 17490 kopiyky
    assert p.ean == "05053990161669"
    assert p.quantity == Decimal("0.165")             # from title, not weight=240.0
    assert p.unit == "kg" and p.brand and p.url.startswith("https://")
    assert p.unit_price == Decimal("1060.00")
    assert all(p.price > 0 for p in ps), "no zero/negative prices"

    # weighted goods: price is per kg, and the "ean" is a fake chain-prefixed id
    loose = next(p for p in (_zakaz_product("metro", it)
                             for it in load("zakaz_metro_банан.json")["results"])
                 if p.name == "Банан")
    assert loose.ean is None, "fake ean 'metro2889...' must not be stored"
    assert (loose.quantity, loose.unit) == (Decimal(1), "kg")
    assert loose.unit_price == Decimal("59.90")
    print(f"  zakaz  {len(ps)} products OK")
    return ps


def test_silpo():
    raw = load("silpo_pringles.json")["items"]
    ps = [_silpo_product(it) for it in raw]
    p = next(p for p in ps if "паприка" in p.name.lower())
    assert p.price == Decimal("189.90"), p.price       # float UAH, not kopiyky
    assert p.quantity == Decimal("0.165")              # from displayRatio "165г"
    assert p.ean is None                               # endpoint exposes none
    assert p.brand == "Pringles"
    assert p.shop == "silpo"
    print(f"  silpo  {len(ps)} products OK")
    return ps


def test_atb():
    """ATB is HTML-scraped; the fixture is a real 799 KB category page."""
    page = (FIX / "atb_category_page.html").read_text(encoding="utf-8")
    arts = _ARTICLE.findall(page)
    ps = [p for a in arts if (p := _atb_product(a))]
    assert len(ps) == len(arts) == 36, f"{len(ps)} parsed of {len(arts)} articles"

    p = next(p for p in ps if p.shop_sku == "47555")
    assert p.price == Decimal("62.90")
    assert p.old_price == Decimal("104.90")            # __bottom, higher = old
    assert p.quantity == Decimal("0.300")              # "300 г" in the title
    assert p.url.startswith("https://www.atbmarket.com/product/")
    assert p.name.startswith("Готовий"), p.name        # unescaped, collapsed
    assert all(x.price > 0 for x in ps)
    assert all(x.old_price is None or x.old_price > x.price for x in ps), \
        "old_price must never be below the current price"

    # brand is absent from ATB's HTML — it gets borrowed from other shops
    assert all(x.brand is None for x in ps)
    assert models.guess_brand("Приправа 45г Приправка Exclusive для риби",
                              {"Приправка", "Pringles"}) == "Приправка"
    assert models.guess_brand("Молоко 900г", {"Pringles"}) is None
    assert models.guess_brand("x", {None, "ab"}) is None   # short/None ignored
    print(f"  atb    {len(ps)} products OK")
    return ps


def test_cross_shop_match(zakaz, silpo):
    """The point of the project: same product, two shops, comparable."""
    keys = {p.match_key for p in zakaz} & {p.match_key for p in silpo}
    assert keys, "Pringles 165g must cluster across shops despite Silpo having no EAN"
    m = next(p for p in zakaz if p.match_key in keys)
    s = next(p for p in silpo if p.match_key in keys)
    assert m.price != s.price
    print(f"  matched on {m.match_key!r}: metro {m.price} vs silpo {s.price}")


def test_wholesale():
    """zakaz `price_wholesale` = buy-more-pay-less. 42 of 153 fixture items."""
    raw = load("zakaz_novus_pringles.json")["results"]
    ps = [_zakaz_product("novus", it) for it in raw]
    t = next(p for p in ps if p.price_tiers)
    assert t.price_at(1) == t.price > t.price_at(2) >= t.price_at(100)
    assert all(q > 0 and pr > 0 for q, pr in t.price_tiers)
    assert list(t.price_tiers) == sorted(t.price_tiers), "tiers must ascend by qty"
    n = sum(1 for p in ps if p.price_tiers)
    print(f"  tiers  {n}/{len(ps)} items have multi-buy pricing, "
          f"e.g. {t.price} -> {t.price_at(2)} @2")
    return ps


def test_db(products):
    con = db.connect(":memory:")
    assert db.save(con, products) == len(products)
    db.save(con, products)  # idempotent: re-running a scrape must not duplicate
    assert con.execute("SELECT count(*) FROM products").fetchone()[0] == len(products)
    assert db.compare(con, "pringles"), "compare() found nothing"
    assert db.compare(con, "чипси"), "Cyrillic search must fold case (SQLite lower() is ASCII-only)"

    # buying 2 must never cost more per item than buying 1, and should be
    # strictly cheaper somewhere — otherwise tiers aren't reaching the query
    one = {r[1]: r[2] for r in db.compare(con, "pringles", 1)}
    two = {r[1]: r[2] for r in db.compare(con, "pringles", 2)}
    assert all(two[k] <= one[k] for k in one), "qty=2 got more expensive"
    assert any(two[k] < one[k] for k in one), "tiers never applied in compare()"
    print(f"  db     {len(products)} rows, idempotent, qty tiers reach compare()")


if __name__ == "__main__":
    models.demo()
    z, s, a = test_zakaz(), test_silpo(), test_atb()
    w = test_wholesale()
    test_cross_shop_match(z, s)
    test_db(z + s + a + w)
    print("all green")
