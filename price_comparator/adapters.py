"""One adapter per API platform. Both yield the same Product.

zakaz.ua covers metro/novus/auchan/megamarket/ekomarket/... (21 chains).
Silpo is its own stack. ATB is neither — see probe_atb.py.

Stdlib only, sequential. A watchlist is ~1 request per shop per keyword;
concurrency would be complexity with nothing to buy it.
"""

import gzip
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from decimal import Decimal
from functools import lru_cache

from .config import ATB_CATEGORIES, ATB_COOKIE, SILPO_BRANCH, ZAKAZ_STORES
from .models import Product, parse_quantity

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def _get_text(url, headers=None, retries=3):
    """GET with backoff on 429/5xx. Raises on permanent failure."""
    req = urllib.request.Request(url, headers={
        "Accept": "*/*", "Accept-Language": "uk", "Accept-Encoding": "gzip",
        "User-Agent": UA, **(headers or {})})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw.decode("utf-8")
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == retries - 1:
                raise
        except urllib.error.URLError:
            if attempt == retries - 1:
                raise
        time.sleep(2 ** attempt)


def _get(url, headers=None):
    return json.loads(_get_text(url, {"Accept": "application/json", **(headers or {})}))


# ------------------------------------------------------------------ zakaz.ua

ZAKAZ = "https://stores-api.zakaz.ua"


def zakaz_search(chain, query):
    store = ZAKAZ_STORES[chain]
    url = f"{ZAKAZ}/stores/{store}/search/?q={urllib.parse.quote(query)}"
    data = _get(url, {"X-Chain": chain, "X-Version": "1"})
    # ponytail: first page only (~30 of `count` hits), relevance-ranked.
    # Enough for a watchlist. Add ?page= when you switch to category walks.
    return [_zakaz_product(chain, it) for it in data.get("results", [])]


def _zakaz_product(chain, it):
    qty, unit = parse_quantity(it.get("title"))
    api_unit = it.get("unit")
    if qty is None and api_unit in ("kg", "l"):
        # Weighted goods ("Банан", unit=kg, weight=0): price is already per kg.
        qty, unit = Decimal(1), api_unit
    ean = it.get("ean") or ""
    if not ean.isdigit():
        ean = None   # weighted SKUs carry a fake id like "metro28896500000000"
    disc = it.get("discount") or {}
    old = disc.get("old_price") if disc.get("status") else None
    # "take 2 for 161.10 each, 19 for 152.15" — kopiyky, same as `price`
    tiers = tuple(sorted((int(t["min_qty"]), Decimal(t["price"]) / 100)
                         for t in (it.get("price_wholesale") or [])
                         if t.get("min_qty") and t.get("price")))
    return Product(
        shop=chain,
        shop_sku=str(it.get("sku") or it.get("ean")),
        name=it.get("title", ""),
        price=Decimal(it["price"]) / 100,          # kopiyky -> UAH
        old_price=Decimal(old) / 100 if old else None,
        quantity=qty,
        unit=unit or (api_unit if api_unit in ("kg", "l", "pcs") else None),
        ean=ean,
        brand=(it.get("producer") or {}).get("trademark"),
        category=it.get("category_id", ""),
        url=it.get("web_url", ""),
        in_stock=bool(it.get("in_stock")),
        price_tiers=tiers,
    )


# --------------------------------------------------------------------- Silpo

SILPO = "https://sf-ecom-api.silpo.ua/v1/uk"


def silpo_search(query, limit=100):
    """Silpo paginates properly: limit/offset/total."""
    out, offset = [], 0
    while True:
        url = (f"{SILPO}/branches/{SILPO_BRANCH}/products"
               f"?limit={limit}&offset={offset}&search={urllib.parse.quote(query)}")
        data = _get(url)
        items = data.get("items", [])
        out += [_silpo_product(it) for it in items]
        offset += limit
        if not items or offset >= min(data.get("total", 0), 300):
            return out


def _silpo_product(it):
    if it.get("weighted"):
        # ponytail: weighted items send ratio="кг" but displayRatio="100г" —
        # the two disagree about what `price` is per. Leave quantity unset so
        # they drop out of unit-price ranking rather than rank on a guess.
        # Resolve by opening one on silpo.ua and comparing the shelf price.
        qty, unit = None, None
    else:
        # displayRatio is net content ("165г"); title often omits it.
        qty, unit = parse_quantity(it.get("displayRatio") or "")
        if qty is None:
            qty, unit = parse_quantity(it.get("title"))
    return Product(
        shop="silpo",
        shop_sku=str(it.get("externalProductId") or it.get("id")),
        name=it.get("title", ""),
        price=Decimal(str(it["price"])),           # already UAH
        old_price=Decimal(str(it["oldPrice"])) if it.get("oldPrice") else None,
        quantity=qty,
        unit=unit,
        ean=None,                                  # not exposed by this endpoint
        brand=it.get("brandTitle"),
        category=it.get("sectionSlug", ""),
        url=f"https://silpo.ua/product/{it.get('slug', '')}",
        in_stock=bool(it.get("stock", 0)),
    )


# ----------------------------------------------------------------------- ATB

# ATB is not on zakaz.ua and has no JSON API. The catalog is server-rendered and
# prices sit in <data value="62.90" class="product-price__top"> — a real value
# attribute, not display text. Regex over <article> blocks is enough; no HTML
# parser, no BeautifulSoup, no headless browser.
#
# robots.txt Allows /catalog/* and /product/* but Disallows /*search* and
# /*per-page=* — so we walk categories and filter locally. Do not add a search
# call here.

ATB = "https://www.atbmarket.com"
_ARTICLE = re.compile(r'<article class="\s*catalog-item.*?</article>', re.S)
_ATB = {
    "pid":   re.compile(r"wishlist\?id=(\d+)"),
    "url":   re.compile(r'href="(/product/[^"]+)"'),
    "title": re.compile(r'catalog-item__title[^>]*>\s*<a[^>]*>(.*?)</a>', re.S),
    "price": re.compile(r'<data value="([\d.]+)"[^>]*product-price__top'),
    "old":   re.compile(r'<data value="([\d.]+)"[^>]*product-price__bottom'),
    "unit":  re.compile(r'product-price__unit">/([^<]+)<'),
}
_LAST_PAGE = re.compile(r"\?page=(\d+)")


def _atb_product(article):
    f = {k: (m.group(1).strip() if (m := p.search(article)) else None)
         for k, p in _ATB.items()}
    if not (f["price"] and f["title"]):
        return None
    title = re.sub(r"\s+", " ", html.unescape(f["title"])).strip()
    price = Decimal(f["price"])
    old = Decimal(f["old"]) if f["old"] else None
    if old is not None and old <= price:
        old = None          # __bottom is only an old price when it's higher
    if f["unit"] in ("кг", "л"):
        qty, unit = Decimal(1), "kg" if f["unit"] == "кг" else "l"
    else:
        qty, unit = parse_quantity(title)
    return Product(
        shop="atb", shop_sku=f["pid"] or f["url"], name=title,
        price=price, old_price=old, quantity=qty, unit=unit,
        ean=None,          # ATB publishes no barcode
        brand=None,        # nor a brand field — backfilled in cli.scrape
        category="", url=ATB + (f["url"] or ""), in_stock=True,
    )


@lru_cache(maxsize=None)
def atb_category(slug, max_pages=20):
    """Every product in one ATB category. Cached: the watchlist reuses pages."""
    headers = {"Accept": "text/html"}
    if ATB_COOKIE:                     # selects the city's store; see config.py
        headers["Cookie"] = ATB_COOKIE
    out, page = [], 1
    while page <= max_pages:
        url = f"{ATB}{slug}" + (f"?page={page}" if page > 1 else "")
        htm = _get_text(url, headers)
        found = [p for a in _ARTICLE.findall(htm) if (p := _atb_product(a))]
        out += found
        pages = [int(n) for n in _LAST_PAGE.findall(htm)]
        if not found or page >= max(pages, default=1):
            break
        page += 1
    return out


def atb_search(query):
    q = query.lower()
    return [p for slug in ATB_CATEGORIES
            for p in atb_category(slug) if q in p.name.lower()]


# ------------------------------------------------------------------ registry

SHOPS = {chain: (lambda q, c=chain: zakaz_search(c, q)) for chain in ZAKAZ_STORES}
SHOPS["silpo"] = silpo_search
SHOPS["atb"] = atb_search
