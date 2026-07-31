# Price Comparator — Implementation Plan

**Scope:** CLI / data pipeline. Ukrainian grocery chains. Python only, **zero dependencies** (stdlib `urllib` + `sqlite3`).
**City:** Вінниця.
**Data scope:** a **keyword watchlist**, not full catalogs. Expanding = adding a string to `config.WATCHLIST`.
**Not in scope (yet):** web UI, API server, user accounts.

**Status: Phase 0 done, Phase 1 done for 2 platforms. ATB open.**

---

## 0. Phase 0 results (measured, not guessed)

`probe.py` ran against live APIs. Fixtures in `tests/fixtures/`.

| Platform | Chains | Status |
|---|---|---|
| **zakaz.ua** — `stores-api.zakaz.ua/stores/{id}/search/?q=` | **21 chains** incl. METRO, NOVUS, Auchan, MegaMarket, EkoMarket, Ultramarket, WineTime, Tavria V | ✅ working, EAN included |
| **Silpo** — `sf-ecom-api.silpo.ua/v1/uk/branches/{branch}/products?search=` | Silpo | ✅ working, **no EAN** |
| **ATB** — `www.atbmarket.com/catalog/{slug}?page=N`, HTML | ATB | ✅ working, **HTML scrape**, no EAN, no brand |
| **Fora** | — | not probed yet |

**ATB is confirmed absent from the zakaz.ua platform** (21 chains listed, none of them ATB) and has **no JSON API** — `/api/v1/catalog` and `/api/catalog` return 200 but serve HTML, and every `zakaz.atbmarket.com` path 404s. So it's a third adapter, scraping server-rendered HTML.

That turned out cheap. Prices are in a real `value` attribute, not display text:

```html
<article class="catalog-item js-product-container">
  <a href="/site/wishlist?id=47555">            <!-- product id -->
  <a href="/product/{slug}">{title}</a>
  <data value="62.90"  class="product-price__top">     <!-- current -->
  <data value="104.90" class="product-price__bottom">  <!-- old -->
  <span class="product-price__unit">/шт</span>
```

Six regexes over `<article>` blocks, all six fields present in 36/36 items. **No BeautifulSoup, no headless browser, no lxml.** Pagination is `?page=N`; the nav exposes the last page number.

**robots.txt shapes the design.** ATB explicitly `Allow`s `/catalog/*` and `/product/*` but `Disallow`s `/*search*` and `/*per-page=*`. So ATB walks configured categories and filters titles locally — deliberately *not* a search call. `atb_category()` is `@lru_cache`d so the whole watchlist shares one fetch of each category.

**Category slugs come in two shapes** — `/catalog/398-moloko` *and* `/catalog/cipsi`. My first probe regex required the numeric prefix and silently dropped **46 of 204** categories, including every snack section (`cipsi`, `cipsi-sneki`, `sneki`). Fixed in `probe_atb.py`, and `atb_categories.txt` has been regenerated from the saved HTML — 204 slugs now, no re-probe needed. `python -m price_comparator categories <keyword>` greps it.

> `tests/fixtures/atb_stores.json` is a red herring — the `?chain=atb` filter was ignored and it's a byte-identical copy of `zakaz_stores.json`. Delete it.

### Payload differences that bite

| | zakaz.ua | Silpo | ATB |
|---|---|---|---|
| price | `int` **kopiyky** (`17490`) | `float` **UAH** (`189.9`) | `str` UAH in `<data value>` |
| multi-buy | `price_wholesale` tiers | none | none |
| EAN | yes — but weighted goods get a fake `metro28896500000000` | **none**, listing *or* detail | **none** |
| brand | `producer.trademark` | `brandTitle` | **no field at all** |
| net weight | **not** `weight` (that's shipping weight: `240.0` for a 165 g tube) — parse the title | `displayRatio` = `"165г"` | parse the title |
| weighted goods | `unit:"kg"`, `weight:0`, price per kg | `weighted:true`, `ratio`/`displayRatio` disagree — unresolved | `product-price__unit` = `/кг` |
| pagination | first page only, ~30 of `count` | proper `limit`/`offset`/`total` | `?page=N`, last page in nav |
| discovery | `category_results` gives matching slugs | — | robots forbids search; walk categories |

### Vinnytsia coverage is thin

Counted from `zakaz_stores.json`, not assumed. Of 21 zakaz chains, **only two serve Вінниця**:

| Shop | How | ID |
|---|---|---|
| METRO | zakaz.ua | `48215621` |
| Grono | zakaz.ua | `482476001` — `city` is `null` in the API, only `address.city` says Вінниця |
| Silpo | own API | branch `1edb6b53-…` (вул. Зодчих 2); 9 Vinnytsia branches exist |
| ATB | HTML | **not yet city-locked** — see below |

Novus, Auchan, MegaMarket, EkoMarket and Ultramarket are Kyiv/Lviv/Odesa only. Kyiv gets 12 chains; Вінниця gets one. Nothing to do about that — it's what the platform carries.

**ATB store is the loose end.** Pages default to `data-store="1154"` and the city `<select>` gives Вінниця `data-cityid="583"`, but switching city is an AJAX call that sets a cookie. Rather than guess the cookie name, `config.ATB_COOKIE` takes a raw cookie string — grab it from DevTools after picking Вінниця on the site. Left `None`, ATB prices are *some other store's*.

### Multi-buy pricing

zakaz exposes `price_wholesale` — 42 of 153 fixture items carry it:

```json
"price_wholesale": [{"min_qty": 2, "price": 16110}, {"min_qty": 19, "price": 15215}]
```

Kopiyky, per item, not per lot. Stored on `Product.price_tiers`, applied by `price_at(qty)`, and `compare` takes a quantity:

```
python -m price_comparator compare pringles      # silpo 159.00 wins
python -m price_comparator compare 2 pringles    # novus 152.10 wins
```

That flip is the point — the cheapest shop for one item is not the cheapest for two. Silpo and ATB publish no tiers, so they compete on their single-item price at every quantity.

### Consequence for matching

Silpo having no barcode — **confirmed on the detail endpoint too**, not just the listing — kills the EAN-first plan. An EAN-preferring key would isolate Silpo in its own bucket forever. The join key is **`brand + net quantity`**, with EAN kept as a separate column for the stronger zakaz-to-zakaz join. Verified: `bq:pringles:0.165:kg` clusters 25 offers across metro, novus and silpo.

ATB has no brand field either, so `guess_brand()` borrows brand names other shops already contributed to the DB — longest substring match against the title. ATB is scraped last in the registry for exactly this reason.

> **Unproven.** The mechanism is unit-tested, but the ATB fixture is `/285-bakaliya` (spices, cereals) while the other fixtures are banana/milk/pringles — zero brand overlap, so 0/36 backfilled in the offline run. It will only prove itself on a real run where the categories overlap. Expect it to catch national brands (Pringles, Nestlé) and miss ATB private label — but private label can't match across shops anyway.

---

## 1. Layout

```
price_comparator/
  config.py        # WATCHLIST + store IDs — the only file you edit routinely
  models.py        # Product, parse_quantity, match_key
  adapters.py      # zakaz_search + silpo_search + SHOPS registry
  db.py            # sqlite schema, save, compare
  cli.py
tests/
  fixtures/        # real payloads from probe.py
  test_adapters.py # parses them offline — no network
probe.py           # Phase 0 (done)
probe_atb.py       # Phase 0b (open)
```

*ponytail: `adapters.py` is one file, not a package — both adapters are ~40 lines. Split when a third one makes it unpleasant, which ATB might.*

Adapter contract is a **function**, not a class hierarchy:

```python
SHOPS = {chain: (lambda q, c=chain: zakaz_search(c, q)) for chain in ZAKAZ_STORES}
SHOPS["silpo"] = silpo_search
```

*ponytail: no ABC, no factory, no ShopConfig class. `str -> list[Product]` is the whole interface.*

## 2. The normalized model

Implemented in `models.py`. `Decimal` for money, never float. Two derived properties do the real work:

- **`unit_price`** — UAH per kg/l/pcs. The only honest comparison when Pringles ships as 165 g in one shop and 158 g in another.
- **`match_key`** — `brand + quantity + unit`. See the Phase 0 note above for why this is *not* EAN-first.

`parse_quantity` reads the title, not the API's weight field, and takes the **last** match so `"Молоко 2,5% 950г"` yields 0.950 kg rather than 2.5.

## 3. Storage

SQLite, two tables:

Two tables (see `db.py`): `products` keyed on `(shop, shop_sku)`, `prices` keyed on `(shop, shop_sku, day)`. Re-scraping upserts products and appends one price row per day — price history comes free, and re-running the same day is idempotent.

**Gotcha found in testing:** SQLite's `lower()` and `LIKE` fold **ASCII only**, so `lower('Молоко')` ≠ `lower('молоко')` and every Cyrillic search silently returned zero rows. Fixed by registering Python's Unicode-aware `str.lower` as a SQL function:

```python
con.create_function("lower_u", 1, lambda s: s.lower() if s else s)
```

*ponytail: SQLite. Move to Postgres when you have concurrent writers or the file passes ~10 GB — years away at watchlist volume.*

---

## Phases

### ✅ Phase 0 — Probe — **done**

`probe.py` ran live. Results in section 0 above, payloads in `tests/fixtures/`.

### ✅ Phase 1–3 — zakaz.ua + Silpo adapters — **done**

```bash
python tests/test_adapters.py             # offline, parses fixtures, no network
python -m price_comparator scrape         # METRO + Grono + Silpo + ATB × WATCHLIST
python -m price_comparator compare pringles
python -m price_comparator compare 2 pringles   # applies multi-buy tiers
python -m price_comparator categories cipsi     # grep ATB slugs
```

All adapters emit the same `Product`.

### ✅ Phase 3b — ATB — **done**

HTML adapter, 36/36 items parsed from the real category page. Set `ATB_CATEGORIES` to the sections you shop from before the first real run — the three defaults are guesses, and **no top-level chips category exists** in ATB's 157 slugs, so Pringles may need hunting (`409-krekeri` is the nearest candidate).

Not used, though available: `sitemap_products.xml` lists every product URL. That's the door to a full ATB catalog if the watchlist ever stops being enough — at the cost of one fetch per product.

### ⬜ Phase 4 — Better matching — only if needed

`brand + quantity` already clusters 25 Pringles offers across 3 shops. Escalate only when it measurably misses:

1. Manual mapping table for the ~200 items you actually buy. Boring, works, no ML.
2. Fuzzy title matching (`rapidfuzz`) with a confidence score and a review queue.

Private label never matches across shops — accept it. Loose produce has no brand, so it falls back to unit price within a category.

*ponytail: no embeddings until 1–2 measurably fail on your data.*

### ⬜ Phase 5 — Scheduling

`schtasks` / cron, one run a day. Not Airflow.

---

## Risks

| Risk | Mitigation |
|---|---|
| Endpoints change without notice (all undocumented) | Fixture-based tests fail loudly; keep adapters ~100 lines each so a rewrite is cheap |
| IP block / Cloudflare | Modest concurrency, real User-Agent, respect 429. Residential UA proxy only if actually blocked |
| Prices vary by store/city | Store ID is part of config, not hardcoded. Consider scoping the DB to one city first |
| Legal / ToS | Public catalog data, personal use — but read each site's `robots.txt` and ToS before running daily at scale |
| Volume | Watchlist ≈ 30–300 rows per shop per run. Trivial |
| **zakaz search returns only ~30 of `count` hits** | Fine for a watchlist. Switch to category walks (`category_results` gives the slugs) when you want a whole section |
| **Silpo weighted goods** | `ratio` says кг, `displayRatio` says 100г. `quantity` left unset so they never rank on a guessed unit — verify one on silpo.ua and fix |
| Search is fuzzy | "банан" returns banana-flavoured yoghurt. Filter on `category_id` if noise bothers you |
| **ATB HTML changes** | The 6 regexes are the fragile part. `test_atb()` asserts 36/36 parse against a saved page — it fails loudly, and re-saving the fixture is the whole fix |
| ATB category costs ~16 fetches | `@lru_cache` on `atb_category` so all watchlist keywords share one pass. Keep `ATB_CATEGORIES` short |

## Deliberately skipped

- **No dependencies at all** — `urllib` + `sqlite3`. No httpx, no BeautifulSoup, no ORM, no pytest.
- No async/concurrency — a watchlist run is a few dozen sequential requests.
- No Docker, no Airflow, no proxy pool, no web UI.

## Known open items

1. **`ATB_COOKIE`** — until it's set, ATB prices are not Vinnytsia prices.
2. **Grono is untested** — it's the one chain whose `city` field is `null`; the `X-Chain: grono` header may behave differently. The scrape prints per-shop counts, so a zero there is the tell.
3. **Silpo branch switch untested** — moved off the all-branches sentinel to a real Vinnytsia branch; confirm `search` still returns results.
4. **ATB brand backfill unproven** — see the note in section 0.
5. **Fora** — not probed. Would add a second Vinnytsia option if it works.
6. **Silpo weighted-goods unit** — one manual check on silpo.ua.
7. Schema changed (`prices.tiers`). No `prices.db` exists yet, so nothing to migrate — but if one appears before the next schema change, delete it rather than migrate.

## What's in git

`.gitignore` covers the generated stuff: `*.db`, `__pycache__/`, editor dirs, plus two dead fixtures (`silpo_branches.json`, `atb_stores.json`) that were already committed and are now untracked.

**Fixtures are committed on purpose.** They're inputs, not artifacts — `test_adapters.py` parses five of them offline, and they're the only warning system for a silent upstream format change. `atb_categories.txt` is read at *runtime* by `categories`, so it isn't test-only despite living under `tests/`.

`.gitattributes` marks `tests/fixtures/**` as `-text`. They're byte-exact captures of what the shops served; without it, writing them on Windows produced CRLF against git's stored LF and all 16 showed as fully modified on every checkout.
