"""Client-generic account discovery via Claude web search.

Generalises the Oangle-specific `lead_finder.search_segment` to ANY client: the
system + search prompts are built from the client profile plus the run's
industries + market (+ optional size band). Returns account dicts in a common
shape the enrichment/export stages understand.
"""

import json
import re
import sys

import anthropic

MODEL = "claude-sonnet-4-6"   # discovery draft model (web search is the bottleneck)
MAX_SEARCH = 3                # cap web searches per industry query (cost + latency)
MAX_TOKENS = 3072


def _system(profile: dict) -> str:
    bands = ", ".join(profile["heat_ranks"])
    grant = profile.get("grant_logic")
    return (
        f"You are a lead-generation analyst for {profile['client_name']}.\n"
        f"{profile['client_one_liner']}\n\n"
        f"Ideal customer profile: {profile['icp']}\n"
        f"Value proposition: {profile['value_prop']}\n\n"
        f"{profile['scoring_logic']}\n\n"
        + (f"GRANT ELIGIBILITY: {grant}\n\n" if grant else "")
        + "Find real, currently-operating companies that fit the ICP. Exclude any "
        "that clearly fail it. Ground every company in a real source page via "
        f"live web search, and assign exactly one priority band from: {bands}. "
        "Return ONLY a valid JSON array — no preamble, no explanation."
    )


def _user(industry: str, market: str, size_band: str, n: int,
          want_grant: bool = False) -> str:
    size_line = f"- Prefer company size: {size_band}\n" if size_band else ""
    grant_line = ('    "grant_eligible": "Y|N",\n' if want_grant else "")
    return (
        f"Find up to {n} companies in the \"{industry}\" industry"
        + (f" located in {market}" if market else "")
        + " that fit the ICP.\n"
        + size_line
        + "Research a real source page for each (official site, news, filings). "
        "Return ONLY a JSON array of objects with this exact shape:\n"
        "[\n"
        "  {\n"
        '    "company_name": "Legal or common company name",\n'
        '    "brand_name": "Consumer-facing brand name (may equal company_name)",\n'
        '    "website": "https://official-site.com",\n'
        '    "source_url": "https://... (page the details came from)",\n'
        f'    "sector": "{industry}",\n'
        '    "company_size": "employee band, e.g. 100-499",\n'
        f'    "market": "{market or "?"}",\n'
        '    "priority_band": "exactly one of the allowed bands",\n'
        '    "why_it_scores": "1-2 sentences of concrete signals (PR, hiring, '
        'funding, tech, expansion) that drove the band",\n'
        '    "likely_use_case": "the most likely workload / engagement angle for '
        'our offering",\n'
        + grant_line +
        '    "confidence": "high|med|low"\n'
        "  }\n"
        "]"
    )


def apply_account_flags(profile: dict, accounts: list[dict]) -> list[dict]:
    """Set warm_intro / grant_eligible on discovered accounts.

    warm_intro is decided HERE, not by the model: a partner-brand match is a
    hard fact from the client profile, and the highest-value signal in the list
    (a warm intro beats any cold-outreach score), so it must not depend on the
    model happening to notice. grant_eligible comes back from the model when the
    client has grant_logic; we only normalise it to Y/N.

    Clients with neither configured get neither key, so their exports stay clean.
    """
    brands = [b.strip().lower() for b in profile.get("warm_intro_brands", []) if b.strip()]
    want_grant = bool(profile.get("grant_logic"))
    for a in accounts:
        if brands:
            hay = f"{a.get('brand_name', '')} {a.get('company_name', '')}".lower()
            a["warm_intro"] = "Y" if any(b in hay for b in brands) else "N"
        if want_grant:
            a["grant_eligible"] = "Y" if str(a.get("grant_eligible", "")).strip().upper().startswith("Y") else "N"
    return accounts


def _parse(text: str) -> list[dict]:
    try:
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if m:
            arr = json.loads(m.group())
            if isinstance(arr, list):
                return [a for a in arr if isinstance(a, dict)]
    except json.JSONDecodeError as exc:
        print(f"  [WARN] discovery JSON parse error: {exc}", file=sys.stderr)
        print(f"  [RAW]  {text[:300]}", file=sys.stderr)
    return []


def _domain_key(a: dict) -> str:
    raw = (a.get("website") or a.get("company_name") or "").strip().lower()
    return (raw.replace("https://", "").replace("http://", "")
               .replace("www.", "").split("/")[0])


def discover_industry(client: anthropic.Anthropic, profile: dict, industry: str,
                      market: str, size_band: str, n: int,
                      usage_acc: dict | None = None) -> list[dict]:
    """Agentic web-search discovery for ONE industry; returns account dicts.

    Uses the STREAMING API: web-search calls are long-running, and a blocking
    request times out (~100s) before the agentic search finishes. Streaming keeps
    the connection alive so the request completes reliably.
    """
    system = _system(profile)
    user = _user(industry, market, size_band, n, want_grant=bool(profile.get("grant_logic")))
    msgs = [{"role": "user", "content": user}]
    final = None
    for _ in range(4):
        with client.messages.stream(
            model=MODEL, max_tokens=MAX_TOKENS, system=system,
            tools=[{"type": "web_search_20260209", "name": "web_search",
                    "max_uses": MAX_SEARCH}],
            messages=msgs) as stream:
            for _event in stream:   # drain events to keep the connection alive
                pass
            final = stream.get_final_message()
        if usage_acc is not None:
            import cost_model
            cost_model.add_usage(usage_acc, final.usage)
        if final.stop_reason != "pause_turn":
            break
        msgs = [{"role": "user", "content": user},
                {"role": "assistant", "content": final.content}]
    text = "".join(b.text for b in final.content if b.type == "text") if final else ""
    out = _parse(text)
    for a in out:
        a.setdefault("segment", industry)
        a.setdefault("brand_name", a.get("company_name", ""))
        a["market"] = a.get("market") or market or ""
    return apply_account_flags(profile, out)


def discover_accounts(client: anthropic.Anthropic, profile: dict,
                      industries: list[str], market: str, size_band: str,
                      max_accounts: int, usage_acc: dict | None = None,
                      on_event=None) -> list[dict]:
    """Discover across up to 3 industries, dedupe by domain, cap to max_accounts."""
    industries = [i for i in industries if i.strip()][:3]
    per = max(2, -(-max_accounts // max(1, len(industries))))  # ceil split
    seen: set[str] = set()
    out: list[dict] = []
    for ind in industries:
        if on_event:
            on_event({"stage": "discover", "msg": f"searching: {ind}"})
        for a in discover_industry(client, profile, ind, market, size_band, per, usage_acc):
            key = _domain_key(a)
            if key and key not in seen:
                seen.add(key)
                out.append(a)
            if len(out) >= max_accounts:
                return _warm_first(out)
    return _warm_first(out)


def _warm_first(accounts: list[dict]) -> list[dict]:
    """Partner-brand accounts to the top — a warm intro outranks a cold lead."""
    return sorted(accounts, key=lambda a: a.get("warm_intro") != "Y")
