"""The whole scope knob. Expanding coverage = editing these lists."""

CITY = "Вінниця"

# What to track. Search-based: one request per shop per keyword.
# ponytail: keywords, not category trees. Category slugs are chain-suffixed
# ("uht-milk-metro" vs "molochni-i-syrkovi-deserty-4990") so a category
# allowlist needs a per-chain mapping table. Keywords need nothing.
WATCHLIST = [
    "банан",
    "молоко",
    "pringles",
]

# --- zakaz.ua platform -------------------------------------------------------
# Vinnytsia coverage is thin. Of the 21 chains in tests/fixtures/zakaz_stores.json
# only these two serve Вінниця — everything else (Novus, Auchan, MegaMarket,
# EkoMarket, Ultramarket) is Kyiv/Lviv/Odesa only. Verified against the fixture,
# not assumed.
ZAKAZ_STORES = {
    "metro": "48215621",   # METRO Вінниця
    "grono": "482476001",  # Grono, вул. Зодчих 16 — `city` is null in the API,
                           # only address.city says Вінниця. Watch this one.
}

# --- Silpo -------------------------------------------------------------------
# 9 Vinnytsia branches in the fixture. Using вул. Зодчих 2 (ext 2086).
# The all-branches sentinel 00000000-...-0000 gives nationwide pricing instead.
SILPO_BRANCH = "1edb6b53-596c-6d06-b5f0-b5ff7ea46636"

# --- ATB ---------------------------------------------------------------------
# No search API, and robots.txt disallows /*search* — so we walk categories and
# filter locally. Full slug list: tests/fixtures/atb_categories.txt.
# Note slugs come in two shapes: "/catalog/398-moloko" and "/catalog/cipsi".
# Each category costs ~16 page fetches, so keep this list short.
ATB_CATEGORIES = [
    "/catalog/398-moloko",
    "/catalog/288-frukti-yagodi",
    "/catalog/cipsi",
]

# ATB serves per-store prices. Pages default to data-store="1154"; Вінниця is
# data-cityid="583" in the city <select>. The switch is an AJAX call that sets a
# cookie, so paste yours here to get Vinnytsia prices:
#   DevTools -> Application -> Cookies -> www.atbmarket.com, after picking Вінниця
# Left None = whatever ATB's default store is, which is NOT Vinnytsia.
ATB_COOKIE = None
ATB_CITY_ID = 583
