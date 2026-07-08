#!/usr/bin/env python3
"""
Oangle Lead Finder — F&B Vertical (MVP Phase 1)

Searches the web for F&B prospects in Singapore and produces a ranked,
research-backed CSV lead list ready for the Oangle sales team.

Usage:
    python lead_finder.py

Environment:
    ANTHROPIC_API_KEY  — required
"""

import anthropic
import csv
import json
import re
import sys
from datetime import datetime

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MODEL = "claude-opus-4-8"
# Faster model for the interactive single-company scoring (the live "Find leads"
# path in the viewer). The web-search loop is the latency bottleneck, so a
# quicker model here roughly halves time-to-result with no real quality loss on
# a one-company fit assessment. Batch discovery still uses MODEL (Opus) above.
SCORE_MODEL = "claude-sonnet-4-6"

MAX_LEADS_PER_SEGMENT = 5       # Raise to get more leads (raises API cost)
MIN_FIT_SCORE = 2               # Drop leads below this score before export (0 = keep all)
REQUIRE_SOURCE_URL = False      # If True, discard leads with no source_url

TARGET_SEGMENTS = [
    "fast food chains",
    "bubble tea and kiosk outlets",
    "quick service restaurants (QSR)",
    "cloud kitchens",
    "cafe chains",
    "food courts",
]

OANGLE_PRODUCTS = [
    "POS",
    "SOK (self-ordering kiosk)",
    "ORB (order relay board)",
    "DMB (digital menu board)",
]

# Brands reachable via the kitchen-equipment-supplier partnership channel.
# Leads matching these names get warm_intro=Y and should be worked ahead of
# cold leads of the same score.
WARM_INTRO_BRANDS = {"subway", "guzman y gomez", "guzman", "kfc"}

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a lead generation specialist for Oangle, a Singapore-based technology company.
Oangle provides restaurant technology: POS systems, Self-Ordering Kiosks (SOK), Order Relay Boards (ORB), and Digital Menu Boards (DMB).

HARD DISQUALIFIERS — exclude these entirely, do NOT include them in the output:
- Single-outlet independent restaurants (only one location)
- Pop-ups, temporary stalls, or market vendors
- Businesses operating outside Singapore
- Operators with no verifiable online presence

IDEAL CUSTOMER PROFILE:
- Chain or multi-outlet F&B operator in Singapore
- Standardised, fixed menu — NOT complex made-to-order
- Best fits: fast food chains, bubble tea shops, self-serve kiosks, QSRs, food courts
- AVOID: Complex Chinese-style restaurants, fine dining, omakase, highly variable menus

FIT SCORE RUBRIC (assign a single integer 1–5):
5 — IDEAL: Multi-outlet SG chain (10+ outlets), fully standardised menu, high volume, clear fit for full POS+SOK+ORB+DMB suite. Enterprise/QSR profile like Chick-fil-A.
4 — STRONG: Established SG chain (4–9 outlets), standardised menu, fits POS + at least one of SOK/ORB/DMB.
3 — MODERATE: Small chain (2–3 outlets) or kiosk format, standardised menu, lighter package fit (e.g. POS + ORB).
2 — WEAK: Borderline format or menu complexity, limited outlets, only partial product fit.
1 — POOR: Meets search segment but little genuine fit (low volume, semi-variable menu, unclear expansion).

GRANT ELIGIBILITY: Flag grant_eligible=Y if AI customisation of the workflow would be a natural add-on (e.g. predictive ordering, personalised DMB content) — this lets the contract potentially qualify for Singapore government grants and increases deal value.

CONFIDENCE: Rate how certain you are about the outlet count and website you found:
- high: directly from official website or press release
- med: from aggregator or secondary source
- low: estimated or inferred

Always return only a valid JSON array — no preamble, no explanation."""


def _search_prompt(segment: str) -> str:
    return (
        f'Search for {MAX_LEADS_PER_SEGMENT} F&B businesses in Singapore '
        f'in the "{segment}" segment that are good prospects for Oangle\'s '
        f"restaurant technology (POS, self-ordering kiosks, digital menu boards).\n\n"
        f"EXCLUDE: single-outlet independents, pop-ups, non-Singapore operators, "
        f"businesses with no online presence.\n\n"
        f"Focus on chains with multiple outlets, standardised menus, and high transaction volume.\n\n"
        f"Return ONLY a JSON array with this exact structure (no other text):\n"
        f"[\n"
        f"  {{\n"
        f'    "company_name": "Legal entity name",\n'
        f'    "brand_name": "Consumer brand name",\n'
        f'    "num_outlets_sg": 12,\n'
        f'    "website": "https://example.com",\n'
        f'    "source_url": "https://... (page where you found outlet count / details)",\n'
        f'    "fit_score": 4,\n'
        f'    "fit_reason": "One sentence explaining which signals drove the score",\n'
        f'    "confidence": "high",\n'
        f'    "grant_eligible": "Y",\n'
        f'    "suggested_products": ["POS", "SOK"],\n'
        f'    "segment": "{segment}"\n'
        f"  }}\n"
        f"]"
    )


# ---------------------------------------------------------------------------
# Core agent
# ---------------------------------------------------------------------------


def search_segment(client: anthropic.Anthropic, segment: str) -> list[dict]:
    """Run an agentic web search for one segment, return a list of lead dicts."""
    user_prompt = _search_prompt(segment)
    messages = [{"role": "user", "content": user_prompt}]

    max_continuations = 5
    for _ in range(max_continuations):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                tools=[{"type": "web_search_20260209", "name": "web_search"}],
                messages=messages,
            )
        except anthropic.APIStatusError as exc:
            raise RuntimeError(
                f"Anthropic API error {exc.status_code}: {exc.message}"
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise RuntimeError(f"Anthropic connection error: {exc}") from exc

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason == "pause_turn":
            # Server-side search loop hit its iteration cap; resume by
            # re-sending the original user turn + the assistant's partial work.
            messages = [
                {"role": "user", "content": user_prompt},
                {"role": "assistant", "content": response.content},
            ]
            continue

        break  # Unexpected stop reason

    text = "".join(
        block.text for block in response.content if block.type == "text"
    )
    return _parse_leads(text, segment)


def _parse_leads(text: str, segment: str) -> list[dict]:
    """Extract and validate the JSON array from the model's text response."""
    try:
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            leads = json.loads(match.group())
            if isinstance(leads, list):
                return leads
    except json.JSONDecodeError as exc:
        print(f"  [WARN] JSON parse error for '{segment}': {exc}", file=sys.stderr)
        print(f"  [RAW]  {text[:400]}", file=sys.stderr)
    return []


def score_company(client: anthropic.Anthropic, name: str, website: str,
                  segment: str = "") -> dict:
    """Score ONE named company against the Oangle rubric (web search).

    Used by the interactive viewer so a user-entered company gets the same
    account-context fields (outlets, fit score, suggested products, grant flag)
    that the segment-discovery path produces. Returns {} on failure.
    """
    user = (
        f'Assess this specific Singapore F&B company for Oangle: "{name}" ({website}).\n'
        "Search the web for its OFFICIAL Singapore website, outlet count, menu "
        "format, and technology fit. For official_website, return the real, "
        "currently-working homepage URL you find via search — not a guess and not "
        "the URL given above unless you confirm it is correct.\n"
        "Return ONLY this JSON object (no other text):\n"
        '{"official_website": "https://...", "num_outlets_sg": 0, '
        '"fit_score": 1, "fit_reason": "one sentence", '
        '"confidence": "high|med|low", "grant_eligible": "Y|N", '
        '"suggested_products": ["POS","SOK"], "segment": "' + segment + '"}'
    )
    messages = [{"role": "user", "content": user}]
    response = None
    for _ in range(2):   # cap search/continuation rounds — speed over exhaustive search
        response = client.messages.create(
            model=SCORE_MODEL, max_tokens=2048, system=SYSTEM_PROMPT,
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": 2}],
            messages=messages)
        if response.stop_reason == "end_turn":
            break
        if response.stop_reason == "pause_turn":
            messages = [{"role": "user", "content": user},
                        {"role": "assistant", "content": response.content}]
            continue
        break
    text = "".join(b.text for b in response.content if b.type == "text") if response else ""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            obj = json.loads(m.group())
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    return {}


def _domain_of(url: str) -> str:
    """Bare host (no scheme, no www, no path) for comparing two URLs."""
    u = (url or "").strip()
    if "://" in u:
        u = u.split("://", 1)[1]
    host = u.split("/")[0].split(":")[0]
    return host.removeprefix("www.").lower()


def resolve_url(provided: str, found: str = "") -> tuple[str, str]:
    """Return (best_url, note) — the most accurate website for a company.

    Contact lookup (Hunter/Apollo) is domain-keyed, so a wrong or missing URL
    silently yields zero contacts. The authoritative signal is the official site
    the model finds via live web search (`found`); it confirms or corrects what
    the user typed (`provided`) — e.g. typed "hans.com.sg" → real "hans.sg".

    We deliberately do NOT gate on an HTTP request: many real sites block bot
    user-agents (403) or aren't on HTTPS, so a fetch is a poor correctness test
    and would reject valid domains. Web search is the better check.
    """
    def _norm(u: str) -> str:
        u = (u or "").strip()
        if u and "://" not in u:
            u = "https://" + u
        return u

    p, f = _norm(provided), _norm(found)
    p_dom, f_dom = _domain_of(p), _domain_of(f)

    if f_dom:                                  # model found an official site
        if p_dom and f_dom != p_dom:
            return f, f"corrected → {f_dom} (you entered {p_dom})"
        if not p_dom:
            return f, f"found official site → {f_dom}"
        return f, "URL confirmed by web search"
    return p, ("using entered URL (web search found none)" if p_dom else "no URL provided")


# ---------------------------------------------------------------------------
# Post-processing
# ---------------------------------------------------------------------------


def post_process(leads: list[dict]) -> list[dict]:
    """Deduplicate, enrich with warm_intro, apply score/URL filters, sort."""
    # Add warm_intro flag
    for lead in leads:
        brand = lead.get("brand_name", "").strip().lower()
        lead["warm_intro"] = "Y" if any(w in brand for w in WARM_INTRO_BRANDS) else "N"

    # Deduplicate by brand_name (case-insensitive), first occurrence wins
    seen: set[str] = set()
    unique: list[dict] = []
    for lead in leads:
        key = lead.get("brand_name", "").strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(lead)

    # Apply MIN_FIT_SCORE filter
    if MIN_FIT_SCORE > 0:
        unique = [l for l in unique if int(l.get("fit_score", 0)) >= MIN_FIT_SCORE]

    # Apply REQUIRE_SOURCE_URL filter
    if REQUIRE_SOURCE_URL:
        unique = [l for l in unique if l.get("source_url", "").startswith("http")]

    # Sort by fit_score desc, then warm_intro (Y first)
    unique.sort(key=lambda x: (-(x.get("fit_score") or 0), x.get("warm_intro") != "Y"))

    return unique


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

CSV_FIELDS = [
    "company_name",
    "brand_name",
    "num_outlets_sg",
    "website",
    "source_url",
    "fit_score",
    "fit_reason",
    "confidence",
    "grant_eligible",
    "warm_intro",
    "suggested_products",
    "segment",
]


def write_csv(leads: list[dict], filename: str | None = None) -> str:
    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"leads_fnb_{timestamp}.csv"
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for lead in leads:
            row = dict(lead)
            products = row.get("suggested_products", [])
            row["suggested_products"] = (
                ", ".join(products) if isinstance(products, list) else str(products)
            )
            writer.writerow(row)
    return filename


def print_table(leads: list[dict]) -> None:
    print(f"\n{'BRAND':<30} {'OUTLETS':>7} {'SCORE':>5} {'WARM':>5} {'CONF':>5}  SEGMENT")
    print("-" * 78)
    for lead in leads:
        print(
            f"{lead.get('brand_name', 'N/A'):<30}"
            f" {str(lead.get('num_outlets_sg', '?')):>7}"
            f" {str(lead.get('fit_score', '?')):>5}"
            f" {lead.get('warm_intro', 'N'):>5}"
            f" {lead.get('confidence', '?'):>5}"
            f"  {lead.get('segment', '')}"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    client = anthropic.Anthropic()

    print("Oangle Lead Finder — F&B Singapore")
    print(f"Model : {MODEL}")
    print(f"Segments: {len(TARGET_SEGMENTS)}  |  Max/segment: {MAX_LEADS_PER_SEGMENT}  |  Min score: {MIN_FIT_SCORE}")
    print("=" * 60)

    all_leads: list[dict] = []

    for segment in TARGET_SEGMENTS:
        print(f"\n→ Searching: {segment} ...")
        leads = search_segment(client, segment)
        print(f"  {len(leads)} leads found")
        all_leads.extend(leads)

    all_leads = post_process(all_leads)

    print(f"\n{'=' * 60}")
    print(f"Total unique leads: {len(all_leads)}")
    print_table(all_leads)

    filename = write_csv(all_leads)
    print(f"\nSaved → {filename}")


if __name__ == "__main__":
    main()
