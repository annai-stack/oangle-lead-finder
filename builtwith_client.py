"""
BuiltWith technographics — optional enrichment signal.

For an F&B prospect, the technologies its website runs are a qualification
signal: a competing POS / online-ordering / kiosk vendor → *displacement* play;
none → *greenfield* play. Either way it sharpens the "how to target" guidance.

Two data tiers, auto-selected:
  * Domain API (v21)  — needs paid API credits; returns specific VENDOR names
    (Oddle, Toast, GrabFood, …) → precise competitor detection.
  * Free API (free1)  — no credits; returns only technology GROUPS/CATEGORIES
    (ecommerce, payment, …) → coarse "has commerce/ordering stack vs greenfield".

Returns "" on any error/empty so the caller degrades to no-tech enrichment.
"""

import requests

DOMAIN_API = "https://api.builtwith.com/v21/api.json"
FREE_API   = "https://api.builtwith.com/free1/api.json"

# Vendor names (Domain API tier) that compete with Oangle's stack.
COMPETITOR_TECH = {
    "pos / ordering": [
        "toast", "square", "revel", "touchbistro", "lightspeed", "storehub",
        "qashier", "oddle", "aigens", "foodzaps", "eats365", "feedme", "megapos",
        "tabsquare", "chope", "weeloy",
    ],
    "delivery / aggregator": [
        "grabfood", "foodpanda", "deliveroo", "ubereats", "grab", "uber eats",
    ],
    "kiosk / self-order": ["self order", "self-ordering", "kiosk"],
}

# Group/category keywords (Free API tier) that indicate an online
# commerce / ordering / payment capability. Kept deliberately specific —
# bare "delivery" matches Content-Delivery-Network / Email-Delivery, and bare
# "pos" matches unrelated substrings, so both are excluded as too noisy.
COMMERCE_KEYWORDS = [
    "ecommerce", "e-commerce", "shopping cart", "payment processor", "payment",
    "checkout", "online ordering", "food ordering", "reservation",
    "point of sale", "subscription commerce", "shopify", "woocommerce",
]


# ---------------------------------------------------------------------------
# Paid Domain API — vendor-level
# ---------------------------------------------------------------------------

def get_tech_paid(api_key: str, domain: str, timeout: int = 25) -> list[str]:
    """Specific technology vendor names via the Domain API, or [] on error/no-credits."""
    try:
        resp = requests.get(DOMAIN_API, params={"KEY": api_key, "LOOKUP": domain}, timeout=timeout)
        if not resp.ok:
            return []
        data = resp.json()
        if data.get("Errors"):          # e.g. depleted credits
            return []
        names: list[str] = []
        for result in data.get("Results", []):
            for path in (result.get("Result") or {}).get("Paths", []):
                for tech in path.get("Technologies", []):
                    nm = tech.get("Name")
                    if nm and nm not in names:
                        names.append(nm)
        return names
    except Exception:
        return []


def flag_competitors(technologies: list[str]) -> dict:
    """Return {category: [matched vendor names]} for any competitor tech found."""
    lowered = [(t, t.lower()) for t in technologies]
    hits: dict[str, list[str]] = {}
    for category, vendors in COMPETITOR_TECH.items():
        for original, low in lowered:
            if any(v in low for v in vendors):
                hits.setdefault(category, [])
                if original not in hits[category]:
                    hits[category].append(original)
    return hits


# ---------------------------------------------------------------------------
# Free API — group/category-level (no credits)
# ---------------------------------------------------------------------------

def get_groups_free(api_key: str, domain: str, timeout: int = 25) -> list[str]:
    """Live group + category labels via the Free API, or [] on error."""
    try:
        resp = requests.get(FREE_API, params={"KEY": api_key, "LOOKUP": domain}, timeout=timeout)
        if not resp.ok:
            return []
        j = resp.json()
        if j.get("Errors"):
            return []
        labels: list[str] = []
        for g in j.get("groups", []):
            if g.get("live", 0) > 0:
                labels.append(g.get("name", ""))
            for c in g.get("categories", []):
                if c.get("live", 0) > 0:
                    labels.append(f"{g.get('name','')}/{c.get('name','')}")
        return [x for x in labels if x]
    except Exception:
        return []


def _has_commerce(labels: list[str]) -> list[str]:
    low = [l.lower() for l in labels]
    return sorted({kw for kw in COMMERCE_KEYWORDS if any(kw in l for l in low)})


# ---------------------------------------------------------------------------
# Unified signal — auto-selects the best tier available
# ---------------------------------------------------------------------------

def tech_signals_summary(api_key: str, domain: str) -> str:
    """One-line signal for the company-targeting prompt. Tries the precise
    Domain API first, falls back to the free group/category signal."""
    if not api_key or not domain:
        return ""

    # Tier 1 — precise vendor detection (needs Domain API credits)
    vendors = get_tech_paid(api_key, domain)
    if vendors:
        hits = flag_competitors(vendors)
        if hits:
            parts = [f"{cat}: {', '.join(v)}" for cat, v in hits.items()]
            return f"competing tech detected — {'; '.join(parts)} (displacement angle)"
        return "no competing POS/ordering/kiosk vendor detected (greenfield angle)"

    # Tier 2 — coarse free signal (no credits)
    labels = get_groups_free(api_key, domain)
    if not labels:
        return ""
    commerce = _has_commerce(labels)
    if commerce:
        return (f"online commerce/ordering stack present (signals: {', '.join(commerce)}) "
                "— coarse; upgrade to BuiltWith Domain API for vendor names")
    return "no online commerce/ordering tech detected on the site (greenfield angle, coarse signal)"
