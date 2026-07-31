#!/usr/bin/env python3
"""Phase 0 probe. Finds which endpoints actually work, dumps fixtures, prints prices.

    python probe.py

Run from a Ukrainian IP — these APIs geo-restrict. Stdlib only, no pip install.
Writes tests/fixtures/*.json so later phases parse real payloads, not guesses.
"""

import json
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

FIXTURES = pathlib.Path(__file__).parent / "tests" / "fixtures"
QUERIES = ["банан", "молоко", "pringles"]

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def get(url, headers=None):
    """-> (status, parsed_json_or_text, error_str). Never raises."""
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "Accept-Language": "uk",
        "User-Agent": UA,
        **(headers or {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read().decode("utf-8", "replace")
            try:
                return r.status, json.loads(raw), None
            except json.JSONDecodeError:
                return r.status, raw[:400], "not json"
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:400], f"HTTP {e.code}"
    except Exception as e:  # DNS, TLS, timeout, geo-block
        return None, None, f"{type(e).__name__}: {e}"


def save(name, data):
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / f"{name}.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"    saved tests/fixtures/{name}.json")


def shape(obj, depth=0):
    """One-line-per-key sketch of a payload, so we learn the real field names."""
    pad = "  " * depth
    if isinstance(obj, dict):
        for k, v in list(obj.items())[:25]:
            kind = type(v).__name__
            preview = "" if isinstance(v, (dict, list)) else f" = {str(v)[:60]}"
            print(f"{pad}  {k}: {kind}{preview}")
            # nested one more level: zakaz wraps price/weight in sub-objects
            if isinstance(v, (dict, list)) and depth < 3:
                shape(v, depth + 1)
    elif isinstance(obj, list) and obj:
        shape(obj[0], depth)


# ---------------------------------------------------------------- zakaz.ua

ZAKAZ = "https://stores-api.zakaz.ua"


def probe_zakaz():
    print("\n=== zakaz.ua platform ===")

    # 1. Store list -> chain names + store IDs. Everything else needs an ID.
    for path in ("/stores/", "/stores"):
        status, data, err = get(ZAKAZ + path)
        print(f"  GET {path} -> {status or err}")
        if status == 200 and isinstance(data, (list, dict)):
            save("zakaz_stores", data)
            break
    else:
        print("  !! could not list stores; everything below will likely fail")
        data = None

    store_ids = []
    if isinstance(data, list):
        print("\n  chains found:")
        for s in data[:40]:
            sid = s.get("id") or s.get("store_id")
            print(f"    {sid}  {s.get('retail_chain', '?'):<14} {s.get('city', {}) if isinstance(s.get('city'), dict) else s.get('city', '')}")
            if sid:
                store_ids.append((s.get("retail_chain", "?"), str(sid)))

    if not store_ids:
        # ponytail: hardcoded fallbacks so the probe still produces something
        # if /stores/ is blocked. Replace with real IDs from the listing above.
        store_ids = [("metro", "48215611"), ("novus", "48246401")]
        print(f"\n  using fallback store IDs: {store_ids}")

    # 2. Search — path shape is the main unknown. Try candidates, keep what works.
    chain, sid = store_ids[0]
    candidates = [
        f"{ZAKAZ}/stores/{sid}/search/?q={{q}}",
        f"{ZAKAZ}/stores/{sid}/products/search/?q={{q}}",
        f"{ZAKAZ}/stores/{sid}/search/?q={{q}}&per_page=10",
    ]
    working = None
    for tmpl in candidates:
        status, data, err = get(tmpl.format(q=urllib.parse.quote("банан")),
                                headers={"X-Chain": chain, "X-Version": "1"})
        print(f"\n  GET {tmpl.split(sid)[1][:45]:<45} -> {status or err}")
        if status == 200:
            working = tmpl
            save(f"zakaz_{chain}_banan", data)
            print("    payload shape:")
            items = data.get("results") or data.get("items") or data if isinstance(data, dict) else data
            shape(items[0] if isinstance(items, list) and items else data, 1)
            break

    if not working:
        print("  !! no search path worked — fall back to category walk:")
        print(f"     GET {ZAKAZ}/stores/{sid}/categories/")
        return

    # 3. The three target products, across every chain we found.
    print("\n  --- prices ---")
    for chain, sid in store_ids[:6]:
        for q in QUERIES:
            url = working.replace(store_ids[0][1], sid).format(q=urllib.parse.quote(q))
            status, data, err = get(url, headers={"X-Chain": chain, "X-Version": "1"})
            if status != 200:
                print(f"    {chain:<12} {q:<10} -> {status or err}")
                continue
            items = (data.get("results") or data.get("items") or []) if isinstance(data, dict) else data
            for it in (items or [])[:2]:
                print(f"    {chain:<12} {q:<10} {str(it.get('title', ''))[:42]:<42} "
                      f"{it.get('price', '?')}  ean={it.get('ean') or it.get('barcode') or '-'}")
            save(f"zakaz_{chain}_{q}", data)


# ------------------------------------------------------------------- Silpo

SILPO = "https://sf-ecom-api.silpo.ua/v1/uk"
SILPO_ANY_BRANCH = "00000000-0000-0000-0000-000000000000"


def probe_silpo():
    print("\n=== Silpo (Fozzy) ===")

    status, data, err = get(f"{SILPO}/branches")
    print(f"  GET /branches -> {status or err}")
    branch = SILPO_ANY_BRANCH
    if status == 200:
        save("silpo_branches", data)
        items = data.get("items") if isinstance(data, dict) else data
        if isinstance(items, list) and items:
            branch = items[0].get("id", branch)
            print(f"    using branch {branch}")

    for q in QUERIES:
        url = f"{SILPO}/branches/{branch}/products?limit=5&offset=0&search={urllib.parse.quote(q)}"
        status, data, err = get(url)
        print(f"\n  GET /products?search={q} -> {status or err}")
        if status != 200:
            continue
        save(f"silpo_{q}", data)
        items = data.get("items") if isinstance(data, dict) else data
        if not items:
            print("    (no items — 'search' may not be the right param; try 'filter' or 'q')")
            continue
        if q == QUERIES[0]:
            print("    payload shape:")
            shape(items[0], 1)
        for it in items[:2]:
            print(f"    {str(it.get('title', ''))[:42]:<42} {it.get('price', '?')}  "
                  f"ean={it.get('ean') or it.get('barcode') or '-'}")


# --------------------------------------------------------------------- ATB

def probe_atb():
    print("\n=== ATB (zakaz.ua white-label) ===")
    for url in (
        "https://zakaz.atbmarket.com/api/stores/",
        "https://stores-api.zakaz.ua/stores/?chain=atb",
    ):
        status, data, err = get(url)
        print(f"  GET {url} -> {status or err}")
        if status == 200:
            save("atb_stores", data)
            break
    else:
        print("  ATB may need its own adapter, or sits behind the main /stores/ listing.")


if __name__ == "__main__":
    print(f"writing fixtures to {FIXTURES}")
    probe_zakaz()
    probe_silpo()
    probe_atb()
    print("\nDone. Check tests/fixtures/ — those payloads are what Phase 1 parses.")
    print("If everything returned 403/None: you are not on a Ukrainian IP, or Cloudflare blocked the UA.")
