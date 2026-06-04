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
        domain = parsed.netloc.lstrip("www.").split(":")[0]
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
        # Apollo may mask emails on lower-tier plans — still include the row
        contacts.append({
            "contact_name":  person.get("name") or "",
            "contact_title": person.get("title") or "",
            "contact_email": person.get("email") or "",
            "contact_phone": phone,
            "linkedin_url":  person.get("linkedin_url") or "",
            "source":        "Apollo",
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
        })
    return contacts


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
