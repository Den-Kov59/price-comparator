#!/usr/bin/env python3
"""Phase 0b: find a way into ATB, plus one open Silpo question.

    python probe_atb.py

ATB is NOT on the zakaz.ua platform — tests/fixtures/zakaz_stores.json lists 21
chains and ATB is not among them, so `zakaz.atbmarket.com/api/stores/` 404ing is
expected, not a bug. ATB needs its own adapter. This finds which door is open:

  1. a JSON endpoint the site's own frontend calls
  2. failing that, server-rendered HTML with schema.org JSON-LD (prices live in
     <script type="application/ld+json">, no HTML parsing needed)

Stdlib only. Run from a Ukrainian IP.
"""

import gzip
import json
import pathlib
import re
import urllib.error
import urllib.request

FIXTURES = pathlib.Path(__file__).parent / "tests" / "fixtures"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def fetch(url, accept="*/*"):
    """-> (status, text, error). Never raises."""
    req = urllib.request.Request(url, headers={
        "Accept": accept, "Accept-Language": "uk,en;q=0.9",
        "Accept-Encoding": "gzip", "User-Agent": UA,
        "Referer": "https://www.atbmarket.com/"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return r.status, raw.decode("utf-8", "replace"), None
    except urllib.error.HTTPError as e:
        return e.code, "", f"HTTP {e.code}"
    except Exception as e:
        return None, "", f"{type(e).__name__}: {e}"


def save(name, text):
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / name).write_text(text, encoding="utf-8")
    print(f"      saved tests/fixtures/{name}  ({len(text)//1024} KB)")


# ------------------------------------------------------------- 1. JSON doors

API_CANDIDATES = [
    "https://www.atbmarket.com/api/v1/catalog",
    "https://www.atbmarket.com/api/catalog",
    "https://api.atbmarket.com/api/v1/products",
    "https://zakaz.atbmarket.com/api/v1/stores",
    "https://zakaz.atbmarket.com/api/v2/stores",
    "https://stores-api.zakaz.ua/stores/atb/",
]


def probe_api():
    print("\n=== 1. JSON endpoint candidates ===")
    hits = []
    for url in API_CANDIDATES:
        status, body, err = fetch(url, "application/json")
        looks_json = body.lstrip()[:1] in "[{"
        print(f"  {status or err:<12} {'JSON' if looks_json else '':<5} {url}")
        if status == 200 and looks_json:
            hits.append(url)
            save("atb_api_hit.json", body)
    return hits


# ------------------------------------------------- 2. HTML + schema.org JSON-LD

LD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.DOTALL | re.IGNORECASE)
# ATB category URLs come in TWO shapes: /catalog/353-bakaliya AND /catalog/cipsi.
# The original numeric-only pattern silently dropped 46 of 204 categories,
# including every snack section. Do not put \d+- back.
CAT_RE = re.compile(r'href="(/catalog/[a-z0-9\-]+)"', re.IGNORECASE)
PRICE_RE = re.compile(r'"price"\s*:\s*"?([\d.]+)')


def jsonld(html):
    out = []
    for blob in LD_RE.findall(html):
        try:
            data = json.loads(blob.strip())
        except json.JSONDecodeError:
            continue
        out += data if isinstance(data, list) else [data]
    return out


def probe_html():
    print("\n=== 2. server-rendered HTML ===")
    status, html, err = fetch("https://www.atbmarket.com/catalog", "text/html")
    print(f"  GET /catalog -> {status or err}  ({len(html)//1024} KB)")
    if status != 200:
        print("  !! blocked. Cloudflare, or you are off a Ukrainian IP.")
        return

    cats = sorted(set(CAT_RE.findall(html)))
    print(f"  category links found: {len(cats)}")
    for c in cats[:12]:
        print(f"      {c}")
    if cats:
        save("atb_categories.txt", "\n".join(cats))

    target = cats[0] if cats else "/catalog/353-bakaliya"
    status, page, err = fetch(f"https://www.atbmarket.com{target}", "text/html")
    print(f"\n  GET {target} -> {status or err}")
    if status != 200:
        return
    save("atb_category_page.html", page)

    blocks = jsonld(page)
    print(f"  JSON-LD blocks: {len(blocks)}  types: {[b.get('@type') for b in blocks]}")
    products = [b for b in blocks if b.get("@type") in ("Product", "ItemList")]
    if products:
        print("  -> prices are in JSON-LD. Adapter = fetch page, regex the LD block,")
        print("     json.loads it. No HTML parser, no BeautifulSoup needed.")
        print(json.dumps(products[0], ensure_ascii=False, indent=2)[:900])
    else:
        found = PRICE_RE.findall(page)
        print(f'  no Product JSON-LD; raw "price" occurrences: {len(found)} '
              f'-> {found[:8]}')
        print("  -> fall back to CSS-class scraping of atb_category_page.html")

    # pagination shape — needed to walk a whole category
    for suffix in ("?page=2", "/?page=2", "?PAGEN_1=2"):
        s, p, e = fetch(f"https://www.atbmarket.com{target}{suffix}", "text/html")
        print(f"  paging {suffix:<12} -> {s or e}  "
              f"{'(different page)' if p and p != page else '(same/empty)'}")


def probe_sitemap():
    print("\n=== 3. sitemap (cheapest full product list, if present) ===")
    for url in ("https://www.atbmarket.com/sitemap.xml",
                "https://www.atbmarket.com/robots.txt"):
        status, body, err = fetch(url, "text/xml")
        print(f"  {status or err:<12} {url}")
        if status == 200:
            save(url.rsplit("/", 1)[-1].replace(".xml", "_atb.xml"), body[:200_000])
            print("      " + "\n      ".join(body.splitlines()[:6]))


# ------------------------------------------------------- 4. Silpo open question

def probe_silpo_detail():
    """Does Silpo expose EAN on the product-detail endpoint? The listing does not,
    which currently forces brand+quantity matching instead of a barcode join."""
    print("\n=== 4. Silpo: is there a barcode on the detail endpoint? ===")
    try:
        items = json.loads((FIXTURES / "silpo_pringles.json").read_text(encoding="utf-8"))["items"]
    except (OSError, KeyError, IndexError):
        return print("  run probe.py first")
    pid, slug = items[0]["id"], items[0].get("slug", "")
    base = "https://sf-ecom-api.silpo.ua/v1/uk"
    for url in (f"{base}/branches/00000000-0000-0000-0000-000000000000/products/{pid}",
                f"{base}/products/{pid}",
                f"{base}/products/slug/{slug}"):
        status, body, err = fetch(url, "application/json")
        has = [k for k in ("ean", "barcode", "gtin") if f'"{k}"' in body.lower()]
        print(f"  {status or err:<12} barcode fields: {has or 'none'}  {url[:70]}")
        if status == 200 and has:
            save("silpo_product_detail.json", body)
            print("  -> EAN join is possible for Silpo after all; use it.")
            return
    print("  -> no barcode. brand+quantity matching stays.")


if __name__ == "__main__":
    probe_api()
    probe_html()
    probe_sitemap()
    probe_silpo_detail()
    print("\nPaste the output back and I'll write the ATB adapter.")
