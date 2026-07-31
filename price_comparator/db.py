"""SQLite. Products change rarely, prices change daily — hence two tables."""

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

from .models import tiers_dump, tiers_load

DB_PATH = Path(__file__).resolve().parent.parent / "prices.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
  shop TEXT, shop_sku TEXT, name TEXT, ean TEXT, brand TEXT,
  quantity TEXT, unit TEXT, category TEXT, url TEXT, match_key TEXT,
  PRIMARY KEY (shop, shop_sku)
);
CREATE TABLE IF NOT EXISTS prices (
  shop TEXT, shop_sku TEXT, price TEXT, old_price TEXT,
  in_stock INTEGER, day TEXT, tiers TEXT,
  PRIMARY KEY (shop, shop_sku, day)
);
CREATE INDEX IF NOT EXISTS idx_match ON products(match_key);
"""


def connect(path=DB_PATH):
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    # SQLite's built-in lower()/LIKE fold ASCII only — "Молоко" never matches
    # "молоко". Python's str.lower is Unicode-aware; borrow it.
    con.create_function("lower_u", 1, lambda s: s.lower() if s else s)
    return con


def save(con, products, day=None):
    """Upsert products, append one price row per product per day."""
    day = day or date.today().isoformat()
    rows = list(products)
    con.executemany(
        "INSERT INTO products VALUES (?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(shop, shop_sku) DO UPDATE SET name=excluded.name, "
        "ean=excluded.ean, brand=excluded.brand, quantity=excluded.quantity, "
        "unit=excluded.unit, category=excluded.category, url=excluded.url, "
        "match_key=excluded.match_key",
        [(p.shop, p.shop_sku, p.name, p.ean, p.brand,
          str(p.quantity) if p.quantity else None, p.unit, p.category, p.url,
          p.match_key) for p in rows])
    con.executemany(
        "INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)",
        [(p.shop, p.shop_sku, str(p.price),
          str(p.old_price) if p.old_price else None, int(p.in_stock), day,
          tiers_dump(p.price_tiers)) for p in rows])
    con.commit()
    return len(rows)


def known_brands(con):
    """Brands seen in any shop — used to infer ATB's missing brand field."""
    return {b for (b,) in con.execute(
        "SELECT DISTINCT brand FROM products WHERE brand IS NOT NULL")}


def compare(con, query, qty=1, day=None):
    """Offers matching a name substring, cheapest per unit first.

    `qty` applies multi-buy tiers: at qty=2 a shop with a "2 for 161.10 each"
    tier can beat one that is cheaper for a single item. Tiers are applied in
    Python, not SQL — the tier list is a string and unpacking it in SQL would
    be worse than a sort over a few hundred rows.
    """
    day = day or con.execute("SELECT max(day) FROM prices").fetchone()[0]
    rows = con.execute("""
        SELECT p.shop, p.name, pr.price, p.quantity, p.unit, p.url, pr.tiers
        FROM products p JOIN prices pr USING (shop, shop_sku)
        WHERE pr.day = ? AND pr.in_stock = 1 AND lower_u(p.name) LIKE lower_u(?)
    """, (day, f"%{query}%")).fetchall()

    out = []
    for shop, name, price, quantity, unit, url, tiers in rows:
        eff = min([Decimal(price)] + [p for m, p in tiers_load(tiers) if qty >= m])
        per = eff / Decimal(quantity) if quantity and Decimal(quantity) > 0 else None
        out.append((shop, name, eff, quantity, unit, url, per))
    return sorted(out, key=lambda r: (r[6] is None, r[6] or 0))
