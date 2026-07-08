"""
Contact enrichment — Apollo.io and Hunter.io.
"""

import requests
from urllib.parse import urlparse

# Apollo updated their endpoint in 2025 — old /mixed_people/search is deprecated
APOLLO_SEARCH  = "https://api.apollo.io/api/v1/mixed_people/api_search"
HUNTER_SEARCH  = "https://api.hunter.io/v2/domain-search"

DEFAULT_TITLES = [
    "owner",
    "founder",
    "CEO",
    "COO",
    "managing director",
    "general manager",
    "operations manager",
    "operations director",
    "head of operations",
    "regional manager",
    "CTO",
    "IT manager",
    "technology director",
    "procurement manager",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_domain(website: str) -> str | None:
    if not website or str(website).lower() in ("nan", "none", ""):
        return None
    url = str(website)
    if "://" not in url:
        url = f"https://{url}"
    try:
        parsed = urlparse(url)
        # removeprefix, NOT lstrip: lstrip("www.") strips any leading chars in
        # the set {w, .}, corrupting domains like "wendys.com" -> "endys.com".
        netloc = parsed.netloc.split(":")[0]
        domain = netloc.removeprefix("www.")
        return domain or None
    except Exception:
        return None


def _empty_contact(note: str) -> dict:
    return {
        "Contact Name":  note,
        "Contact Title": "",
        "Email":         "",
        "Phone":         "",
        "LinkedIn":      "",
        "Source":        "",
    }


# ---------------------------------------------------------------------------
# Apollo
# ---------------------------------------------------------------------------

def _apollo_post(api_key: str, payload: dict) -> dict:
    resp = requests.post(
        APOLLO_SEARCH,
        json=payload,
        headers={
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "X-Api-Key": api_key,
        },
        timeout=20,
    )
    if not resp.ok:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        raise requests.HTTPError(f"{resp.status_code} — {detail}", response=resp)
    return resp.json()


def _parse_apollo(data: dict) -> list[dict]:
    # New api_search endpoint returns "contacts"; old mixed_people/search returned "people"
    people = data.get("contacts") or data.get("people") or []
    contacts = []
    for person in people:
        phones = person.get("phone_numbers") or []
        phone  = next((p["sanitized_number"] for p in phones if p.get("sanitized_number")), "")
        org    = person.get("organization") or person.get("account") or {}

        # Location — prefer person-level, fall back to org-level
        loc_parts = [
            person.get("city") or org.get("city") or "",
            person.get("state") or org.get("state") or "",
            person.get("country") or org.get("country") or "",
        ]
        location = ", ".join(p for p in loc_parts if p)

        # Past employment (for the "Past 3 Work Experience" column). Apollo
        # returns employment_history as a list ordered most-recent-first.
        history = []
        for job in (person.get("employment_history") or [])[:3]:
            history.append({
                "title":        job.get("title") or "",
                "organization": job.get("organization_name") or "",
                "start_date":   job.get("start_date") or "",
                "end_date":     job.get("end_date") or "",
            })

        # Apollo may mask emails on lower-tier plans — still include the row
        contacts.append({
            # Core contact fields (consumed by existing enrich_leads flow)
            "contact_name":  person.get("name") or "",
            "contact_title": person.get("title") or "",
            "contact_email": person.get("email") or "",
            "contact_phone": phone,
            "linkedin_url":  person.get("linkedin_url") or "",
            "source":        "Apollo",
            # Firmographic / person fields (consumed by the insights pipeline).
            # Kept as extra keys — old callers ignore them.
            "first_name":      person.get("first_name") or "",
            "last_name":       person.get("last_name") or "",
            "seniority":       person.get("seniority") or "",
            "headline":        person.get("headline") or "",
            "location":        location,
            "company_name":    org.get("name") or "",
            "company_domain":  org.get("primary_domain") or org.get("website_url") or "",
            "company_size":    org.get("estimated_num_employees") or "",
            "industry":        org.get("industry") or "",
            "company_desc":    org.get("short_description") or "",
            "employment_history": history,
        })
    return contacts


def search_contacts_apollo(
    api_key: str,
    domain: str,
    titles: list[str] | None = None,
    max_results: int = 5,
) -> list[dict]:
    payload: dict = {
        "q_organization_domains": domain,
        "page": 1,
        "per_page": max_results,
    }
    if titles:
        payload["person_titles"] = titles
    return _parse_apollo(_apollo_post(api_key, payload))


def search_contacts_apollo_by_name(
    api_key: str,
    company_name: str,
    titles: list[str] | None = None,
    max_results: int = 5,
) -> list[dict]:
    payload: dict = {
        "q_keywords": f"{company_name} Singapore",
        "page": 1,
        "per_page": max_results,
    }
    if titles:
        payload["person_titles"] = titles
    return _parse_apollo(_apollo_post(api_key, payload))


# ---------------------------------------------------------------------------
# Hunter.io
# ---------------------------------------------------------------------------

def search_contacts_hunter(
    api_key: str,
    domain: str,
    max_results: int = 5,
) -> list[dict]:
    """Find emails at a domain via Hunter.io domain-search."""
    resp = requests.get(
        HUNTER_SEARCH,
        params={"domain": domain, "api_key": api_key, "limit": max_results},
        timeout=15,
    )
    if not resp.ok:
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text
        raise requests.HTTPError(f"{resp.status_code} — {detail}", response=resp)

    contacts = []
    for email_data in resp.json().get("data", {}).get("emails", []):
        first = email_data.get("first_name") or ""
        last  = email_data.get("last_name")  or ""
        name  = f"{first} {last}".strip() or ""
        contacts.append({
            "contact_name":  name,
            "contact_title": email_data.get("position") or "",
            "contact_email": email_data.get("value")    or "",
            "contact_phone": email_data.get("phone_number") or "",
            "linkedin_url":  email_data.get("linkedin") or "",
            "source":        "Hunter",
            # Extra fields for the insights pipeline
            "first_name":    first,
            "last_name":     last,
            "seniority":     email_data.get("seniority") or "",
        })
    return contacts


# ---------------------------------------------------------------------------
# Apollo organization enrichment — real firmographics (industry, size, domain)
# ---------------------------------------------------------------------------

APOLLO_ORG_ENRICH = "https://api.apollo.io/api/v1/organizations/enrich"


def enrich_organization_apollo(api_key: str, domain: str) -> dict:
    """Fetch real firmographics for a company domain via Apollo org-enrich.

    The people-search endpoint masks PII on lower tiers, but org-enrich still
    returns industry, employee count and the canonical domain. Returns {} on
    any error so the caller can fall back gracefully.
    """
    try:
        resp = requests.get(
            APOLLO_ORG_ENRICH,
            params={"domain": domain},
            headers={"Cache-Control": "no-cache", "X-Api-Key": api_key},
            timeout=20,
        )
        if not resp.ok:
            return {}
        org = resp.json().get("organization") or {}
        return {
            "company_name":   org.get("name") or "",
            "company_domain": org.get("primary_domain") or domain,
            "industry":       org.get("industry") or "",
            "company_size":   org.get("estimated_num_employees") or "",
            "location": ", ".join(p for p in (
                org.get("city") or "", org.get("state") or "", org.get("country") or "",
            ) if p),
            "company_desc":   org.get("short_description") or "",
        }
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Combined enrichment
# ---------------------------------------------------------------------------

def enrich_leads(
    leads_df,
    brand_filter: list[str] | None = None,
    titles: list[str] | None = None,
    max_per_company: int = 3,
    progress_callback=None,
    use_keyword: bool = False,
    apollo_key: str | None = None,
    hunter_key: str | None = None,
):
    """
    Enrich leads with contacts from Apollo and/or Hunter.io.
    Returns (contacts_df, debug_log) tuple.
    """
    import pandas as pd

    targets = leads_df if brand_filter is None else leads_df[leads_df["Brand"].isin(brand_filter)]
    targets = targets.drop_duplicates(subset=["Brand"])
    total   = len(targets)
    rows:      list[dict] = []
    debug_log: list[dict] = []   # one entry per company per source

    for i, (_, row) in enumerate(targets.iterrows()):
        brand = row.get("Brand", "")
        if progress_callback:
            progress_callback(i, total, brand)

        base = {
            "Brand":              brand,
            "Company":            row.get("Company", ""),
            "Outlets":            row.get("Outlets", ""),
            "Score":              row.get("Score", ""),
            "Segment":            row.get("Segment", ""),
            "Website":            row.get("Website", ""),
            "Suggested Products": row.get("Products", ""),
            "Grant Eligible":     row.get("Grant", ""),
            "Warm Intro":         row.get("Warm", ""),
        }

        domain   = extract_domain(str(row.get("Website", "")))
        contacts: list[dict] = []

        # --- Apollo ---
        if apollo_key:
            try:
                if use_keyword:
                    found = search_contacts_apollo_by_name(apollo_key, brand, titles, max_per_company)
                elif domain:
                    found = search_contacts_apollo(apollo_key, domain, titles, max_per_company)
                else:
                    found = []
                    rows.append({**base, **_empty_contact("[No website — Apollo skipped]")})
                contacts += found
                debug_log.append({"Brand": brand, "Source": "Apollo", "Domain": domain or "(keyword)",
                                   "Returned": len(found), "Error": ""})
            except Exception as exc:
                debug_log.append({"Brand": brand, "Source": "Apollo", "Domain": domain or "",
                                   "Returned": 0, "Error": str(exc)[:120]})
                rows.append({**base, **_empty_contact(f"[Apollo error: {exc}]")})

        # --- Hunter ---
        if hunter_key:
            if domain:
                try:
                    found = search_contacts_hunter(hunter_key, domain, max_per_company)
                    contacts += found
                    debug_log.append({"Brand": brand, "Source": "Hunter", "Domain": domain,
                                       "Returned": len(found), "Error": ""})
                except Exception as exc:
                    debug_log.append({"Brand": brand, "Source": "Hunter", "Domain": domain,
                                       "Returned": 0, "Error": str(exc)[:120]})
                    rows.append({**base, **_empty_contact(f"[Hunter error: {exc}]")})
            else:
                debug_log.append({"Brand": brand, "Source": "Hunter", "Domain": "",
                                   "Returned": 0, "Error": "No domain — skipped"})

        # Deduplicate by email, keep first occurrence
        seen: set[str] = set()
        for c in contacts:
            email = (c.get("contact_email") or "").lower().strip()
            if email and email in seen:
                continue
            if email:
                seen.add(email)
            rows.append({
                **base,
                "Contact Name":  c["contact_name"],
                "Contact Title": c["contact_title"],
                "Email":         c["contact_email"],
                "Phone":         c["contact_phone"],
                "LinkedIn":      c["linkedin_url"],
                "Data Source":   c.get("source", ""),
            })

        if not contacts and not any(
            r.get("Brand") == brand and str(r.get("Contact Name", "")).startswith("[")
            for r in rows
        ):
            rows.append({**base, **_empty_contact("[No contacts found]")})

    df      = pd.DataFrame(rows)      if rows      else pd.DataFrame()
    debug   = pd.DataFrame(debug_log) if debug_log else pd.DataFrame()
    return df, debug
