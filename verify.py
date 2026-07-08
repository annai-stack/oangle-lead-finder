"""
Verification layer — turn "the model said so" into checkable signals.

The pipeline's promise is *research-backed* leads, so every machine-generated
claim that can be cheaply verified is verified here, and the result is surfaced
as a REAL confidence signal (not the model's self-reported `confidence`).

Three independent checks, combined into one `data_confidence` per record:
  1. Email      — Hunter email-verifier: deliverable / risky / undeliverable.
  2. Source URL — does the discovery `source_url` actually resolve (HTTP 200)?
  3. Claim      — does that page's text support the asserted company + outlet
                  count? (one cheap, no-web-search model call per company.)

Everything degrades gracefully: a network error or missing key lowers
confidence and adds a note, it never raises into the pipeline.
"""

import re

import requests

import insights_enrich as ie

HUNTER_VERIFY = "https://api.hunter.io/v2/email-verifier"
_UA = {"User-Agent": "Mozilla/5.0 (compatible; lead-finder-verify/1.0)"}


# ---------------------------------------------------------------------------
# 1. Email verification (Hunter)
# ---------------------------------------------------------------------------

def verify_email_hunter(api_key: str, email: str, timeout: int = 15) -> dict:
    """Return {status, result, score} for one email.

    result ∈ {deliverable, risky, undeliverable, unknown}
    status ∈ {valid, invalid, accept_all, webmail, disposable, unknown, ...}
    """
    if not email or "@" not in email:
        return {"status": "no_email", "result": "undeliverable", "score": 0}
    try:
        resp = requests.get(HUNTER_VERIFY,
                            params={"email": email, "api_key": api_key},
                            timeout=timeout)
        if not resp.ok:
            return {"status": "error", "result": "unknown", "score": 0}
        d = resp.json().get("data", {}) or {}
        return {
            "status": d.get("status") or "unknown",
            "result": d.get("result") or "unknown",
            "score":  d.get("score", 0) or 0,
        }
    except Exception:
        return {"status": "error", "result": "unknown", "score": 0}


def email_is_sendable(verdict: dict) -> bool:
    """Conservative gate — never auto-send to these."""
    return (verdict.get("result") != "undeliverable"
            and verdict.get("status") not in ("invalid", "disposable"))


# ---------------------------------------------------------------------------
# 2. Source-URL resolution + text fetch
# ---------------------------------------------------------------------------

def url_resolves(url: str, timeout: int = 12) -> tuple[bool, int]:
    """True iff the URL returns a 2xx. Some servers reject HEAD, so use GET."""
    if not url or not str(url).startswith("http"):
        return False, 0
    try:
        resp = requests.get(url, timeout=timeout, headers=_UA, allow_redirects=True)
        return resp.ok, resp.status_code
    except Exception:
        return False, 0


def fetch_text(url: str, timeout: int = 12, max_chars: int = 3000) -> str:
    """Fetch a page and return crudely-stripped visible text (capped)."""
    if not url or not str(url).startswith("http"):
        return ""
    try:
        resp = requests.get(url, timeout=timeout, headers=_UA, allow_redirects=True)
        if not resp.ok:
            return ""
        html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", resp.text,
                      flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:max_chars]
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 3. Claim cross-check (model, no web search → cheap)
# ---------------------------------------------------------------------------

def cross_check_company(client, company: dict, page_text: str,
                        model: str, usage_acc: dict | None = None) -> dict:
    """Ask the model whether `page_text` supports the asserted company facts.

    Returns {supports_company: bool|None, supports_outlets: bool|"unknown"|None,
             note: str}.  None means "could not check" (no page text).
    """
    name    = company.get("company_name") or company.get("brand_name") or ""
    outlets = company.get("num_outlets_sg", "")
    if not page_text:
        return {"supports_company": None, "supports_outlets": None,
                "note": "source URL did not resolve / no readable text"}

    system = (
        "You verify B2B lead facts against a source page. Be strict: only mark a "
        "fact supported if the page text actually backs it. Do not use outside "
        "knowledge. Return ONLY a valid JSON object."
    )
    user = (
        f"Asserted facts about a company:\n"
        f"- Name: {name}\n"
        f"- Outlets in Singapore: {outlets}\n\n"
        f'Source page text (truncated):\n"""{page_text}"""\n\n'
        "Return ONLY this JSON object:\n"
        '{\n'
        '  "supports_company": true or false,\n'
        '  "supports_outlets": true or false or "unknown",\n'
        '  "note": "one short sentence on what the page does/doesn\'t confirm"\n'
        '}'
    )
    raw = ie._run_search(client, system, user, max_tokens=300,
                         usage_acc=usage_acc, model=model, use_tools=False)
    obj = ie._parse_json_object(raw, f"verify:{name}")
    return {
        "supports_company": obj.get("supports_company"),
        "supports_outlets": obj.get("supports_outlets"),
        "note": obj.get("note", ""),
    }


# ---------------------------------------------------------------------------
# Confidence derivation — objective signals → high / med / low
# ---------------------------------------------------------------------------

def derive_confidence(*, identity_anchor: bool, url_ok: bool,
                      claim: dict, email_verdict: dict) -> tuple[str, list[str]]:
    """Combine the checks into a level + human-readable notes."""
    score = 0
    notes: list[str] = []

    if identity_anchor:
        score += 1
    else:
        notes.append("no LinkedIn/seniority anchor for contact")

    if url_ok:
        score += 1
    else:
        notes.append("source URL unreachable")

    sc = claim.get("supports_company")
    if sc is True:
        score += 1
    elif sc is False:
        notes.append("source does not mention this company")

    so = claim.get("supports_outlets")
    if so is True:
        score += 1
    elif so is False:
        notes.append("outlet count not supported by source")

    res = email_verdict.get("result")
    if res == "deliverable":
        score += 1
    elif res == "undeliverable":
        notes.append("email undeliverable")
    elif res == "risky":
        notes.append("email risky (accept-all/unknown)")

    level = "high" if score >= 4 else "med" if score >= 2 else "low"
    return level, notes


# ---------------------------------------------------------------------------
# Top-level pass — path-agnostic (works on records from sync OR batch)
# ---------------------------------------------------------------------------

def verify_records(client, records: list[dict], keys: dict, model: str,
                   check_claims: bool = True, verify_emails: bool = True,
                   usage_acc: dict | None = None) -> dict:
    """Mutate `records` in place with verification fields; return a summary.

    Added per-record keys:
      email_status, email_result, email_score,
      data_confidence (high/med/low), verification_notes, needs_review (bool)

    Caches by email and by company so we never pay twice for the same check.
    """
    hunter_key = keys.get("HUNTER_API_KEY", "")
    email_cache:   dict[str, dict] = {}
    url_cache:     dict[str, bool] = {}
    claim_cache:   dict[str, dict] = {}
    summary = {"deliverable": 0, "risky": 0, "undeliverable": 0, "email_unknown": 0,
               "high": 0, "med": 0, "low": 0, "needs_review": 0}

    for rec in records:
        # --- Email ---
        email = (rec.get("email") or "").strip().lower()
        if verify_emails and hunter_key and email:
            if email not in email_cache:
                email_cache[email] = verify_email_hunter(hunter_key, email)
            verdict = email_cache[email]
        else:
            verdict = {"status": "not_checked", "result": "unknown", "score": 0}
        rec["email_status"] = verdict["status"]
        rec["email_result"] = verdict["result"]
        rec["email_score"]  = verdict["score"]

        # --- Source URL + claim (cached per company) ---
        ckey = (rec.get("company_name") or rec.get("company_domain") or "").lower()
        src  = rec.get("source_url", "")
        if ckey not in url_cache:
            ok, _code = url_resolves(src)
            url_cache[ckey] = ok
            if check_claims:
                page = fetch_text(src) if ok else ""
                claim_cache[ckey] = cross_check_company(
                    client, rec, page, model, usage_acc=usage_acc)
            else:
                claim_cache[ckey] = {"supports_company": None,
                                     "supports_outlets": None, "note": ""}
        url_ok = url_cache[ckey]
        claim  = claim_cache[ckey]

        # --- Combine ---
        identity_anchor = bool(rec.get("linkedin_url") or rec.get("seniority"))
        level, notes = derive_confidence(
            identity_anchor=identity_anchor, url_ok=url_ok,
            claim=claim, email_verdict=verdict)
        if claim.get("note"):
            notes.append(f"source check: {claim['note']}")

        rec["data_confidence"]    = level
        rec["verification_notes"] = " · ".join(notes)
        rec["needs_review"] = (level == "low") or (verdict["result"] == "undeliverable")

        # --- Tally ---
        res = verdict["result"]
        summary[{"deliverable": "deliverable", "risky": "risky",
                 "undeliverable": "undeliverable"}.get(res, "email_unknown")] += 1
        summary[level] += 1
        if rec["needs_review"]:
            summary["needs_review"] += 1

    return summary
