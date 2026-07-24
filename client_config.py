"""
Client profiles for the AI Lead Insights pipeline.

Each profile drives the enrichment prompts and the scoring/heat-rank logic, so
the same pipeline can serve any client. To onboard a new client, add one entry
to CLIENTS below — no other file needs to change.
"""

# ---------------------------------------------------------------------------
# Oangle — Singapore restaurant technology (F&B vertical, Phase 1)
# ---------------------------------------------------------------------------

OANGLE = {
    "client_name": "Oangle",
    "client_one_liner": (
        "Oangle is a Singapore-based restaurant technology company providing "
        "POS systems, Self-Ordering Kiosks (SOK), Order Relay Boards (ORB) and "
        "Digital Menu Boards (DMB) to multi-outlet F&B operators."
    ),
    # Outbound = cold prospecting (no inbound form). Inbound clients can set this
    # to "inbound" so the form-context columns get populated instead of blanked.
    "motion": "outbound",

    # Short description of who we sell to — steers contact-title selection and
    # the "how to target" guidance.
    "icp": (
        "Chain or multi-outlet F&B operators in Singapore with standardised, "
        "fixed menus and high transaction volume — fast food, bubble tea, QSR, "
        "cloud kitchens, cafe chains, food courts. Avoid single-outlet "
        "independents, fine dining, omakase and highly made-to-order menus."
    ),

    # Decision-maker titles to pull from Apollo for each company.
    "target_titles": [
        "owner", "founder", "CEO", "COO", "managing director",
        "general manager", "operations director", "head of operations",
        "regional manager", "IT manager", "technology director",
        "procurement manager",
    ],

    # The customer value proposition the personalised paragraph + CVP column
    # should argue from.
    "value_prop": (
        "Oangle's integrated POS + self-ordering kiosk + digital menu board "
        "suite reduces front-counter labour, shortens queues, lifts average "
        "order value through upsell prompts, and standardises operations across "
        "outlets. AI add-ons (predictive ordering, personalised DMB content) "
        "can qualify the contract for Singapore government technology grants."
    ),

    # Brands reachable through the kitchen-equipment partner — a warm intro is
    # worth more than any cold-outreach signal, so these are flagged
    # deterministically (never left to the model) and sorted to the top.
    # Matched as lowercase substrings against the brand/company name.
    "warm_intro_brands": ["subway", "guzman y gomez", "guzman", "kfc"],

    # When set, discovery is asked to judge grant eligibility and return Y/N.
    # Clients without a grant angle simply omit this key and get no column.
    "grant_logic": (
        "Flag grant_eligible=Y if an AI customisation of the workflow would be a "
        "natural add-on (e.g. predictive ordering, personalised digital-menu-board "
        "content). This can qualify the contract for Singapore government "
        "technology grants and increases deal value. Otherwise N."
    ),

    # Goes verbatim into the 'Sample Scoring Logic' column and is shown to the
    # model so the heat rank it assigns is consistent with the rubric.
    "scoring_logic": (
        "Sample Scoring Logic\n\n"
        "1. ICP / Product Fit (40%)\n"
        "– Multi-outlet, standardised-menu F&B formats score highest\n"
        "– Clear fit for the full POS + SOK + ORB + DMB suite vs. partial fit\n\n"
        "2. Lead Role & Influence (40%)\n"
        "– Decision-making authority (Owner, CEO, COO, Ops/IT Director score highest)\n"
        "– Ability to sponsor a technology rollout budget\n\n"
        "3. Reachability & Intent (20%)\n"
        "– Warm-intro channel (kitchen-equipment partner brands) outranks cold\n"
        "– Expansion signals, recent store openings, existing tech gaps\n\n"
        "Heat Rank tiers:\n"
        "1A – Immediate follow-up: ideal ICP + senior decision-maker\n"
        "1B – Strong: established chain + influential role\n"
        "2  – Moderate: smaller chain or mid-level contact\n"
        "3  – Contextual: borderline fit, nurture\n"
        "4  – Low weight: weak fit, deprioritise"
    ),

    # Allowed heat-rank labels (the model must pick one of these).
    "heat_ranks": ["1A", "1B", "2", "3", "4"],

    # Client-specific account-context columns appended after the 23 reference
    # columns — carried from the account-leads (company discovery) stage.
    # (header, record_key, column_width). Leave empty for a pure reference sheet.
    "account_columns": [
        ("Outlets (SG)",        "num_outlets_sg",     12),
        ("Account Fit Score",   "fit_score",          14),
        ("Suggested Products",  "suggested_products", 22),
        ("Grant Eligible",      "grant_eligible",     14),
        ("Warm Intro",          "warm_intro",         12),
        ("Segment",             "segment",            22),
        ("Account Fit Reason",  "fit_reason",         44),
        ("Account Source URL",  "source_url",         34),
    ],
}


# ---------------------------------------------------------------------------
# AWS × APAC SME — channel-enablement (mirrors the AKIN × AWS deck)
# ---------------------------------------------------------------------------

AWS_APAC = {
    "client_name": "AWS",
    "client_one_liner": (
        "AWS provides cloud infrastructure and services. This engine profiles "
        "APAC SME accounts so AWS channel partners (system integrators) can "
        "engage them for cloud modernisation, scored for readiness."
    ),
    "motion": "outbound",

    "icp": (
        "SME businesses (roughly 100-499 employees) across APAC in cloud-amenable "
        "industries — software & internet, financial services, professional "
        "services — showing migration, modernisation, data/AI or security "
        "signals. Avoid firms already committed to a competing hyperscaler "
        "(Azure/GCP) as a net-new buyer, and non-cloud-amenable sectors."
    ),

    # Decision-makers a partner/SI would engage.
    "target_titles": [
        "CTO", "CIO", "Chief Technology Officer", "Chief Information Officer",
        "Head of Technology", "Head of IT", "Head of Engineering",
        "VP Engineering", "Head of Infrastructure", "Head of Data", "CISO",
        "Head of Security", "Director of Engineering", "IT Director",
        "Head of Platform", "Head of Cloud",
    ],

    "value_prop": (
        "AWS-led modernisation delivered via an SI partner: cloud migration, "
        "data & analytics, AI/ML, application modernisation and security "
        "hardening — turning an account's cloud readiness into a partner-ready, "
        "high-fit engagement."
    ),

    # The deck's transparent 100-point rubric — shown to the model so the
    # priority band it assigns is consistent and explainable.
    "scoring_logic": (
        "Sample Scoring Logic — 100-point readiness rubric (priority order)\n\n"
        "1. Market & Timing (30)\n"
        "   PR, hiring, launches & funding — accessible, scannable signals; the "
        "most reliable read on readiness to engage now.\n"
        "2. Team / Skill & Growth (25)\n"
        "   Size band, leadership & headcount / expansion momentum — from the "
        "About Us / company pages.\n"
        "3. Workload / Use-Case Fit (20)\n"
        "   A clear AWS + SI play — migration, data/analytics, AI/ML, "
        "modernisation — mapped to a real workload.\n"
        "4. Cloud & Tech Readiness (15)\n"
        "   Current stack (BuiltWith), existing cloud / hosting & digital-maturity "
        "signals.\n"
        "5. Data Accessibility (10)\n"
        "   How much can actually be found on the account — completeness of the "
        "public data available.\n\n"
        "Priority bands (assign exactly one):\n"
        "Very High — strong, timely signals across market + workload; engage now.\n"
        "High      — good fit with one or two strong signals; near-term.\n"
        "Medium    — partial fit or thin signals; nurture / SI-led discovery.\n"
        "Low       — weak or negative fit (e.g. competitor-committed); deprioritise."
    ),

    # Allowed priority-band labels (the deck's Very High → Low).
    "heat_ranks": ["Very High", "High", "Medium", "Low"],

    # Account-context columns appended to the reference sheet for this client.
    "account_columns": [
        ("Priority Band",       "priority_band",   14),
        ("Sector",              "sector",          20),
        ("Company Size",        "company_size",    14),
        ("Why It Scores",       "why_it_scores",   48),
        ("Likely AWS/SI Use Case", "likely_use_case", 44),
        ("Market",              "market",          16),
        ("Account Source URL",  "source_url",      34),
    ],
}


# To onboard another client, copy a profile above, edit the fields, and add it
# to CLIENTS below — nothing else needs to change. The UI lists CLIENTS by key.
CLIENTS = {
    "oangle": OANGLE,
    "aws": AWS_APAC,
}


def list_clients() -> list[dict]:
    """[{key, name}] for populating a client picker in the UI."""
    return [{"key": k, "name": v["client_name"]} for k, v in CLIENTS.items()]


def get_client(key: str = "oangle") -> dict:
    """Return a client profile by key (default: oangle)."""
    try:
        return CLIENTS[key.lower()]
    except KeyError:
        raise KeyError(
            f"Unknown client '{key}'. Available: {', '.join(CLIENTS)}"
        ) from None
