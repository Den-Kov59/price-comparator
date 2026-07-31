"""Normalized product model + the messy bits of Ukrainian retail data."""

import re
from dataclasses import dataclass
from decimal import Decimal

# "165г" / "0,95 л" / "1кг" / "10шт" -> (Decimal, base_unit)
_QTY_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(кг|г|мл|л|шт|kg|g|ml|l)\b", re.IGNORECASE)
_TO_BASE = {
    "кг": ("kg", Decimal(1)),   "kg": ("kg", Decimal(1)),
    "г":  ("kg", Decimal("0.001")), "g": ("kg", Decimal("0.001")),
    "л":  ("l",  Decimal(1)),   "l":  ("l",  Decimal(1)),
    "мл": ("l",  Decimal("0.001")), "ml": ("l", Decimal("0.001")),
    "шт": ("pcs", Decimal(1)),
}


def parse_quantity(text):
    """'Чипси Pringles Паприка 165г' -> (Decimal('0.165'), 'kg'). None if absent.

    Titles are the only reliable source: zakaz's `weight` field is *shipping*
    weight (240.0 for a 165 g tube), not net content.
    """
    if not text:
        return None, None
    m = None
    for m in _QTY_RE.finditer(text):  # last match wins: "Молоко 2,5% 950г"
        pass
    if not m:
        return None, None
    unit, factor = _TO_BASE[m.group(2).lower()]
    return Decimal(m.group(1).replace(",", ".")) * factor, unit


def guess_brand(title, known_brands):
    """ATB publishes no brand field, so borrow the brand names other shops do.

    ponytail: reuses data already in the DB instead of a brand dictionary or
    NER. Longest match wins ("Своя лінія" over "Своя"). Misses brands that only
    ATB carries — which is fine, those have nothing to match against anyway.
    """
    t = (title or "").lower()
    hits = [b for b in known_brands if b and len(b) > 2 and b.lower() in t]
    return max(hits, key=len) if hits else None


def tiers_dump(tiers):
    """((2, 161.10), (19, 152.15)) -> '2:161.10;19:152.15'"""
    return ";".join(f"{q}:{p}" for q, p in tiers)


def tiers_load(s):
    if not s:
        return ()
    return tuple((int(q), Decimal(p)) for q, p in
                 (part.split(":") for part in s.split(";")))


@dataclass(frozen=True, slots=True)
class Product:
    shop: str
    shop_sku: str
    name: str
    price: Decimal          # UAH, buying one
    old_price: Decimal | None
    quantity: Decimal | None  # in `unit`
    unit: str | None          # "kg" | "l" | "pcs"
    ean: str | None
    brand: str | None
    category: str
    url: str
    in_stock: bool
    # Multi-buy tiers, cheapest-per-item when you take N: ((min_qty, price), ...)
    # zakaz calls this `price_wholesale`; 42 of 153 fixture items carry one.
    price_tiers: tuple = ()

    def price_at(self, qty=1):
        """Per-item price when buying `qty`. Tiers are per item, not per lot."""
        return min([self.price] + [p for min_qty, p in self.price_tiers
                                   if qty >= min_qty])

    def unit_price_at(self, qty=1):
        """UAH per kg / l / piece — the only honest way to compare 165 g vs 158 g."""
        if not self.quantity or self.quantity <= 0:
            return None
        return (self.price_at(qty) / self.quantity).quantize(Decimal("0.01"))

    @property
    def unit_price(self):
        return self.unit_price_at(1)

    @property
    def match_key(self):
        """Cross-shop join key: brand + net quantity.

        ponytail: NOT ean-first. Silpo's API exposes no barcode at all, so an
        EAN-preferring key would put Silpo in its own bucket forever and the
        two shops would never join — the one thing this project exists to do.
        brand+quantity is the weakest key that spans every shop we have.

        `ean` stays a separate column for the stronger zakaz-to-zakaz join.
        Ceiling: private label never matches (different brands by definition),
        and loose produce has no brand. Add fuzzy title matching when this
        measurably misses something you care about.
        """
        if self.brand and self.quantity:
            return f"bq:{self.brand.lower()}:{self.quantity}:{self.unit}"
        if self.ean:
            return f"ean:{self.ean.lstrip('0')}"
        return f"raw:{self.shop}:{self.shop_sku}"


def demo():
    assert parse_quantity("Чипси Pringles Паприка 165г") == (Decimal("0.165"), "kg")
    assert parse_quantity("Молоко Rioba ультрапастеризоване 2,5% 950г")[0] == Decimal("0.950")
    assert parse_quantity("Олія соняшникова 0,85 л") == (Decimal("0.850"), "l")
    assert parse_quantity("Банани ваговi") == (None, None)
    assert parse_quantity("") == (None, None)

    p = Product("metro", "1", "Чипси Pringles Паприка 165г", Decimal("174.90"), None,
                Decimal("0.165"), "kg", "05053990161669", "Pringles", "snacks", "", True)
    assert p.unit_price == Decimal("1060.00"), p.unit_price

    # multi-buy tiers
    t = Product("novus", "2", "Чипси Pringles Original 165г", Decimal("179.00"), None,
                Decimal("0.165"), "kg", "0505", "Pringles", "snacks", "", True,
                ((2, Decimal("161.10")), (19, Decimal("152.15"))))
    assert t.price_at(1) == Decimal("179.00")
    assert t.price_at(2) == t.price_at(18) == Decimal("161.10")
    assert t.price_at(19) == Decimal("152.15")
    assert t.unit_price_at(19) < t.unit_price_at(1)
    # buying 2 flips who is cheapest: 174.90 vs 179.00 -> 174.90 vs 161.10
    assert p.price_at(2) < t.price_at(1) and t.price_at(2) < p.price_at(2)
    assert tiers_load(tiers_dump(t.price_tiers)) == t.price_tiers
    assert tiers_load("") == () and tiers_dump(()) == ""
    s = Product("silpo", "x", "Чипси Pringles паприка", Decimal("189.90"), None,
                Decimal("0.165"), "kg", None, "Pringles", "snacks", "", True)
    # the whole point: EAN-having and EAN-less shops must land in one bucket
    assert p.match_key == s.match_key == "bq:pringles:0.165:kg"
    loose = Product("metro", "9", "Банани", Decimal("50"), None, None, None,
                    "0482", None, "fruit", "", True)
    assert loose.match_key == "ean:482"                # brandless -> ean fallback
    assert Product("s", "1", "n", Decimal(1), None, None, None, None, None, "c", "", True).unit_price is None
    print("models OK")


if __name__ == "__main__":
    demo()
