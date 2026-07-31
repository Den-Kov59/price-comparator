"""python -m price_comparator scrape
   python -m price_comparator compare pringles
"""

import pathlib
from dataclasses import replace

from . import db
from .adapters import SHOPS
from .config import WATCHLIST
from .models import guess_brand


def scrape():
    con = db.connect()
    total = 0
    # ATB sorts last in the registry on purpose: it publishes no brand field,
    # so it borrows brand names the other shops have already contributed.
    for shop, fetch in SHOPS.items():
        for query in WATCHLIST:
            try:
                products = fetch(query)
            except Exception as e:  # one dead shop must not kill the run
                print(f"  {shop:<12} {query:<10} FAILED  {type(e).__name__}: {e}")
                continue
            if shop == "atb":
                brands = db.known_brands(con)
                products = [replace(p, brand=guess_brand(p.name, brands))
                            for p in products]
            total += db.save(con, products)
            print(f"  {shop:<12} {query:<10} {len(products):>4} items")
    print(f"\n{total} rows -> {db.DB_PATH}")


def compare(query, qty=1):
    rows = db.compare(db.connect(), query, qty)
    if not rows:
        return print(f"nothing matching {query!r} — run `scrape` first?")
    head = f"price x{qty}" if qty > 1 else "price"
    print(f"{'shop':<12} {'product':<44} {head:>9} {'per unit':>13}")
    for shop, name, price, _q, unit, _url, per in rows[:25]:
        print(f"{shop:<12} {name[:44]:<44} {float(price):>9.2f} "
              f"{(f'{float(per):.2f}/{unit}' if per else '-'):>13}")


def categories(keyword=""):
    """List ATB category slugs, so you can pick real ones for config.py."""
    path = pathlib.Path(__file__).resolve().parent.parent / "tests/fixtures/atb_categories.txt"
    if not path.exists():
        return print("run probe_atb.py first")
    slugs = [s for s in path.read_text(encoding="utf-8").split()
             if keyword.lower() in s.lower()]
    print("\n".join(slugs) or f"no category matching {keyword!r}")
