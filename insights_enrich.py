"""
AI Lead Insights — deep enrichment stage.

Takes companies (with their Apollo contacts) and produces the rich, per-contact
records that populate the AI Lead Insights spreadsheet. Enrichment is split:

  * company-level (one web-search call per company) → topline description,
    latest press releases, how-to-target guidance. Shared by all that company's
    contacts so the facts stay consistent.
  * contact-level (one web-search call per contact) → position/bio, past roles,
    person-level press, CVP vs the value prop, personalised sales paragraph,
    and a heat rank scored against the client's rubric.

Firmographics (domain, name, email, seniority, industry, size, location) come
straight from Apollo — we do not spend model calls regenerating them.
"""

import json
import re
import sys

import anthropic

import apollo_client  # extract_domain — for cache keys (no circular import)

# Cost levers ---------------------------------------------------------------
# Model tiering: draft the bulk on the cheaper model; polish only the top-tier
# leads' outreach on the premium model (see polish_outreach).
DRAFT_MODEL   = "claude-sonnet-4-6"   # ~40% cheaper than Opus for the heavy web-search drafting
PREMIUM_MODEL = "claude-opus-4-8"     # used only to refine 1A/1B sales paragraphs (no web search)
MODEL = DRAFT_MODEL                   # back-compat alias

# Bump this whenever a prompt below changes — it invalidates stale cached
# enrichment (db.enrichment_cache keys on it) so reruns regenerate rather than
# serving results produced by an older prompt.
# v2: contact enrichment no longer web-searches (built from Apollo data +
#     company research) — cheaper and removes person-bio hallucination.
PROMPT_VERSION = "v2"


def _norm(s) -> str:
    return (str(s).strip().lower()) if s else ""


def _company_cache_key(company: dict) -> str:
    return (apollo_client.extract_domain(
                company.get("website") or company.get("company_domain") or "")
            or _norm(company.get("brand_name") or company.get("company_name")))


def _contact_cache_key(contact: dict) -> str:
    return (_norm(contact.get("contact_email") or contact.get("email"))
            or _norm(contact.get("linkedin_url")))

# max_uses caps the web searches per call — bounds both the $0.01/search fee
# and (more importantly) the search-result input tokens that dominate cost.
MAX_SEARCH_COMPANY = 3   # 3 sources is enough; the 4th/5th rarely change the result
MAX_SEARCH_CONTACT = 0   # contact enrichment no longer web-searches (see enrich_contact)


# ---------------------------------------------------------------------------
# Shared web-search helper
# ---------------------------------------------------------------------------

def _build_params(system: str, user: str, model: str, max_uses: int,
                  use_tools: bool, max_tokens: int = 2048) -> dict:
    """Build a Messages-API param dict shared by the sync and batch paths.

    Levers baked in: model tiering, max_uses search cap, cache_control on the
    stable system prefix, and use_tools=False for the no-search polish pass.
    """
    system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    tools = ([{"type": "web_search_20260209", "name": "web_search", "max_uses": max_uses}]
             if use_tools else [])
    return {"model": model, "max_tokens": max_tokens, "system": system_blocks,
            "tools": tools, "messages": [{"role": "user", "content": user}]}


def _execute(client: anthropic.Anthropic, params: dict, usage_acc: dict | None = None) -> str:
    """Run a params dict synchronously, resuming on pause_turn; return text."""
    import cost_model
    user_msg = params["messages"][0]
    msgs = [user_msg]
    response = None
    for _ in range(5):
        response = client.messages.create(
            model=params["model"], max_tokens=params["max_tokens"],
            system=params["system"], tools=params["tools"], messages=msgs,
        )
        if usage_acc is not None:
            cost_model.add_usage(usage_acc, response.usage)
        if not params["tools"] or response.stop_reason != "pause_turn":
            break
        msgs = [user_msg, {"role": "assistant", "content": response.content}]
    return extract_text(response) if response is not None else ""


def extract_text(message) -> str:
    """Concatenate text blocks from a (sync or batch) response message."""
    return "".join(b.text for b in message.content if getattr(b, "type", None) == "text")


def _run_search(client: anthropic.Anthropic, system: str, user_prompt: str,
                max_tokens: int = 2048, usage_acc: dict | None = None,
                model: str = DRAFT_MODEL, max_uses: int = MAX_SEARCH_CONTACT,
                use_tools: bool = True) -> str:
    """Synchronous single enrichment call (sugar over _build_params + _execute)."""
    return _execute(client,
                    _build_params(system, user_prompt, model, max_uses, use_tools, max_tokens),
                    usage_acc)


def _parse_json_object(text: str, label: str) -> dict:
    """Extract the first JSON object from model text; {} on failure."""
    try:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            obj = json.loads(match.group())
            if isinstance(obj, dict):
                return obj
    except json.JSONDecodeError as exc:
        print(f"  [WARN] JSON parse error ({label}): {exc}", file=sys.stderr)
        print(f"  [RAW]  {text[:300]}", file=sys.stderr)
    return {}


# ---------------------------------------------------------------------------
# Company-level enrichment
# ---------------------------------------------------------------------------

def _company_prompts(profile: dict, company: dict) -> tuple[str, str]:
    name      = company.get("company_name") or company.get("brand_name") or ""
    website   = company.get("website") or company.get("company_domain") or ""
    cn        = profile["client_name"]
    tech_hint = company.get("tech_signals", "")   # from BuiltWith, if available

    system = (
        f"You are a B2B sales research analyst working for {profile['client_name']}.\n"
        f"{profile['client_one_liner']}\n\n"
        f"Our ideal customer profile: {profile['icp']}\n"
        f"Our value proposition: {profile['value_prop']}\n\n"
        "Research the target company on the web and return concise, factual, "
        "source-grounded summaries. Return ONLY a valid JSON object."
    )
    tech_line = (
        f"Known website technologies (from BuiltWith): {tech_hint}. "
        "Use this to judge whether they already run a competing POS / online-ordering / "
        "kiosk vendor (a displacement angle) or have none (a greenfield angle).\n\n"
        if tech_hint else ""
    )
    user = (
        f"Research the company \"{name}\" ({website}).\n\n"
        + tech_line +
        "Return ONLY this JSON object (no other text):\n"
        "{\n"
        '  "company_topline": "2-3 sentence description of what the company does, '
        'its scale/footprint, and business model.",\n'
        '  "company_press": "2-4 recent news items / press releases about the '
        'company, each as a short numbered sentence. If none found, say so.",\n'
        '  "how_to_target": "3-4 specific, evidence-based angles for how '
        f'{cn} should approach this company, tied to our value proposition'
        + (' and the detected technology stack' if tech_hint else '') + '."\n'
        "}"
    )
    return system, user


def _finalize_company(obj: dict) -> dict:
    return {
        "company_topline": obj.get("company_topline", ""),
        "company_press":   obj.get("company_press", ""),
        "how_to_target":   obj.get("how_to_target", ""),
    }


def enrich_company(client: anthropic.Anthropic, profile: dict, company: dict,
                   usage_acc: dict | None = None, model: str = DRAFT_MODEL,
                   cache=None) -> dict:
    """Return {company_topline, company_press, how_to_target} for one company.

    If `cache` is given (a db.Store), a fresh cached result is returned without
    spending any web-search/model tokens; new results are cached for next time.
    """
    key = _company_cache_key(company) if cache is not None else ""
    if cache is not None and key:
        hit = cache.get("company", key)
        if hit is not None:
            return hit
    system, user = _company_prompts(profile, company)
    name = company.get("company_name") or company.get("brand_name") or ""
    obj = _parse_json_object(
        _run_search(client, system, user, usage_acc=usage_acc,
                    model=model, max_uses=MAX_SEARCH_COMPANY), f"company:{name}")
    result = _finalize_company(obj)
    if cache is not None and key:
        cache.put("company", key, result)
    return result


# ---------------------------------------------------------------------------
# Contact-level enrichment
# ---------------------------------------------------------------------------

def _hist_hint(contact: dict) -> str:
    """Apollo employment history → seed string for the past-experience field."""
    hist = contact.get("employment_history") or []
    return "; ".join(
        f"{h.get('title','')} at {h.get('organization','')}".strip(" at")
        for h in hist if h.get("title") or h.get("organization")
    )


def _contact_prompts(profile: dict, company: dict, contact: dict) -> tuple[str, str]:
    full_name = (contact.get("contact_name")
                 or f"{contact.get('first_name','')} {contact.get('last_name','')}".strip())
    title     = contact.get("contact_title") or contact.get("job_title") or ""
    seniority = contact.get("seniority") or ""
    co_name   = company.get("company_name") or company.get("brand_name") or ""
    linkedin  = contact.get("linkedin_url") or ""
    cn        = profile["client_name"]
    heat_list = ", ".join(profile["heat_ranks"])
    hist_hint = _hist_hint(contact)
    # Company research carried in from enrich_company (passed merged into `company`).
    industry = company.get("industry") or company.get("segment") or ""
    segment  = company.get("segment") or ""
    outlets  = company.get("num_outlets_sg") or ""
    topline  = company.get("company_topline") or ""
    how_to   = company.get("how_to_target") or ""
    press    = company.get("company_press") or ""

    system = (
        f"You are a B2B sales research analyst working for {profile['client_name']}.\n"
        f"{profile['client_one_liner']}\n\n"
        f"Our value proposition: {profile['value_prop']}\n\n"
        f"{profile['scoring_logic']}\n\n"
        "Write personalised, factual outreach using ONLY the facts provided below. "
        "Do NOT invent biographical details, employers, news, or public mentions. "
        "If a field has no supporting fact, return an empty string for it. "
        "Return ONLY a valid JSON object."
    )
    user = (
        "Prepare outreach material for this lead using only the facts given.\n\n"
        "PERSON:\n"
        f"- Name: {full_name}\n"
        f"- Title: {title}\n"
        f"- Seniority: {seniority}\n"
        f"- LinkedIn: {linkedin}\n"
        + (f"- Prior roles (from data provider): {hist_hint}\n" if hist_hint
           else "- Prior roles: (none provided)\n")
        + "\nCOMPANY (already researched):\n"
        f"- Name: {co_name}\n"
        f"- Industry / segment: {industry} / {segment}\n"
        f"- Outlets in Singapore: {outlets}\n"
        f"- Account summary: {topline}\n"
        f"- How to target: {how_to}\n"
        f"- Recent news: {press}\n"
        + "\nReturn ONLY this JSON object (no other text):\n"
        "{\n"
        '  "position_bio": "1-2 sentences on their role and seniority, derived from the '
        'title/seniority above. No invented detail.",\n'
        '  "past_experience": "Their prior roles from the data above as short numbered '
        'lines, or an empty string if none were provided.",\n'
        '  "person_press": "",\n'
        '  "cvp": "Why ' + cn + "'s offering matters to THIS person given their role and "
        'their company (numbered points, grounded in the account summary above).",\n'
        '  "sales_paragraph": "A warm, personalised 3-5 sentence outreach paragraph '
        "addressed by first name, referencing something specific from the company "
        'summary, news, or how-to-target above.",\n'
        f'  "heat_rank": "One of exactly: {heat_list} — scored against the Sample '
        'Scoring Logic, using ICP/product fit (the company) and role & influence (this person)."\n'
        "}"
    )
    return system, user


def _finalize_contact(obj: dict, profile: dict, contact: dict) -> dict:
    heat = str(obj.get("heat_rank", "")).strip()
    if heat not in profile["heat_ranks"]:
        heat = profile["heat_ranks"][-1]  # default to lowest tier if invalid
    return {
        "position_bio":    obj.get("position_bio", ""),
        "past_experience": obj.get("past_experience", "") or _hist_hint(contact),
        "person_press":    obj.get("person_press", ""),
        "cvp":             obj.get("cvp", ""),
        "sales_paragraph": obj.get("sales_paragraph", ""),
        "heat_rank":       heat,
    }


def enrich_contact(client: anthropic.Anthropic, profile: dict,
                   company: dict, contact: dict, usage_acc: dict | None = None,
                   model: str = DRAFT_MODEL, cache=None) -> dict:
    """Return per-contact enrichment for one decision-maker (cache-aware).

    No web search — the bio/past-experience come from Apollo's data and the
    company research is passed in via `company`. This removes ~3 searches and
    most of the per-contact output tokens, and eliminates the person-bio
    hallucination risk (the model is told to use only the provided facts).
    `company` should already include the enrich_company output merged in.
    """
    key = _contact_cache_key(contact) if cache is not None else ""
    if cache is not None and key:
        hit = cache.get("contact", key)
        if hit is not None:
            return hit
    system, user = _contact_prompts(profile, company, contact)
    full_name = (contact.get("contact_name")
                 or f"{contact.get('first_name','')} {contact.get('last_name','')}".strip())
    obj = _parse_json_object(
        _run_search(client, system, user, max_tokens=1024, usage_acc=usage_acc,
                    model=model, use_tools=False), f"contact:{full_name}")
    result = _finalize_contact(obj, profile, contact)
    if cache is not None and key:
        cache.put("contact", key, result)
    return result


# ---------------------------------------------------------------------------
# Two-tier polish — premium model, NO web search (cheap: no search fees, reuses
# facts already gathered). Only worth running on high heat-rank leads.
# ---------------------------------------------------------------------------

def _polish_prompts(profile: dict, company: dict, contact: dict, draft: dict) -> tuple[str, str]:
    full_name = (contact.get("contact_name")
                 or f"{contact.get('first_name','')} {contact.get('last_name','')}".strip())
    co_name = company.get("company_name") or company.get("brand_name") or ""
    cn = profile["client_name"]

    system = (
        f"You are a senior B2B copywriter for {cn}. {profile['client_one_liner']}\n"
        f"Value proposition: {profile['value_prop']}\n\n"
        "Sharpen outreach copy using ONLY the facts provided — do not invent new facts. "
        "Return ONLY a valid JSON object."
    )
    user = (
        f"Lead: {full_name}, {contact.get('contact_title','')} at {co_name}.\n"
        f"Researched facts:\n"
        f"- Bio: {draft.get('position_bio','')}\n"
        f"- Company: {company.get('company_topline','') or draft.get('company_topline','')}\n"
        f"- Current draft CVP: {draft.get('cvp','')}\n"
        f"- Current draft paragraph: {draft.get('sales_paragraph','')}\n\n"
        "Improve both for specificity, warmth and a clear call to action. Return ONLY:\n"
        '{\n  "cvp": "...",\n  "sales_paragraph": "..."\n}'
    )
    return system, user


def _finalize_polish(obj: dict, draft: dict) -> dict:
    out = dict(draft)
    if obj.get("cvp"):
        out["cvp"] = obj["cvp"]
    if obj.get("sales_paragraph"):
        out["sales_paragraph"] = obj["sales_paragraph"]
    return out


def polish_outreach(client: anthropic.Anthropic, profile: dict, company: dict,
                    contact: dict, draft: dict, model: str = PREMIUM_MODEL,
                    usage_acc: dict | None = None, cache=None) -> dict:
    """Rewrite cvp + sales_paragraph on the premium model using the draft's
    already-researched facts. No tools → no search fees, small token count.

    Cache-aware: the polished result is keyed by contact, and since the draft it
    builds on is itself cached + stable, a hit is consistent on rerun.
    """
    key = _contact_cache_key(contact) if cache is not None else ""
    if cache is not None and key:
        hit = cache.get("polish", key)
        if hit is not None:
            return hit
    system, user = _polish_prompts(profile, company, contact, draft)
    full_name = (contact.get("contact_name")
                 or f"{contact.get('first_name','')} {contact.get('last_name','')}".strip())
    obj = _parse_json_object(
        _run_search(client, system, user, usage_acc=usage_acc,
                    model=model, use_tools=False), f"polish:{full_name}")
    result = _finalize_polish(obj, draft)
    if cache is not None and key:
        cache.put("polish", key, result)
    return result


# ---------------------------------------------------------------------------
# Batch param builders — return Messages-API param dicts (no execution) for
# submission via the Batches API. Same prompts/levers as the sync path.
# ---------------------------------------------------------------------------

def company_batch_params(profile: dict, company: dict, model: str = DRAFT_MODEL) -> dict:
    s, u = _company_prompts(profile, company)
    return _build_params(s, u, model, MAX_SEARCH_COMPANY, use_tools=True)


def contact_batch_params(profile: dict, company: dict, contact: dict,
                         model: str = DRAFT_MODEL) -> dict:
    # No web search (see enrich_contact) — `company` must include the company
    # research merged in, since the contact prompt grounds on it.
    s, u = _contact_prompts(profile, company, contact)
    return _build_params(s, u, model, 0, use_tools=False, max_tokens=1024)


def polish_batch_params(profile: dict, company: dict, contact: dict, draft: dict,
                        model: str = PREMIUM_MODEL) -> dict:
    s, u = _polish_prompts(profile, company, contact, draft)
    return _build_params(s, u, model, 0, use_tools=False)


# ---------------------------------------------------------------------------
# Record assembly
# ---------------------------------------------------------------------------

def build_record(profile: dict, company: dict, contact: dict,
                 company_enrich: dict, contact_enrich: dict) -> dict:
    """Merge Apollo firmographics + both enrichment layers into one flat record."""
    inbound = profile.get("motion") == "inbound"
    rec = {
        "company_domain": contact.get("company_domain") or company.get("company_domain")
                           or company.get("website", ""),
        "first_name":     contact.get("first_name", ""),
        "last_name":      contact.get("last_name", ""),
        "linkedin_url":   contact.get("linkedin_url", ""),
        "email":          contact.get("contact_email") or contact.get("email", ""),
        "company_size":   contact.get("company_size") or company.get("company_size", ""),
        "job_title":      contact.get("contact_title") or contact.get("job_title", ""),
        "seniority":      contact.get("seniority", ""),
        "industry":       contact.get("industry") or company.get("industry")
                          or company.get("segment", ""),
        "location":       contact.get("location") or company.get("location", ""),
        "company_name":   company.get("company_name") or company.get("brand_name", ""),
        **company_enrich,
        # Inbound-form columns: blank for outbound motion, available for inbound clients.
        "interest_topic":    contact.get("interest_topic", "") if inbound else "",
        "message_from_lead": contact.get("message_from_lead", "") if inbound else "",
        **contact_enrich,
        # Account-context columns (from the company-discovery stage). Carried so
        # the client-specific account_columns in client_config can export them.
        "num_outlets_sg":     company.get("num_outlets_sg", ""),
        "fit_score":          company.get("fit_score", ""),
        "suggested_products": _join_products(company.get("suggested_products", "")),
        "grant_eligible":     company.get("grant_eligible", ""),
        "warm_intro":         company.get("warm_intro", ""),
        "segment":            company.get("segment", ""),
        "fit_reason":         company.get("fit_reason", ""),
        "source_url":         company.get("source_url", ""),
    }
    # Carry any client-specific account-context fields (from client_config
    # account_columns) straight from the company dict — keeps build_record
    # client-agnostic (e.g. AWS: priority_band, sector, why_it_scores, …).
    for _header, key, _width in profile.get("account_columns", []):
        if key not in rec and company.get(key) not in (None, ""):
            rec[key] = company.get(key)
    return rec


def _join_products(products) -> str:
    """suggested_products may be a list or a string — normalise to a string."""
    if isinstance(products, list):
        return ", ".join(str(p) for p in products)
    return str(products) if products else ""
