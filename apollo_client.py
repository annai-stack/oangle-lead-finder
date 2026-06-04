"""
Apollo.io contact enrichment — people search by company domain.
"""

import requests
from urllib.parse import urlparse

APOLLO_PEOPLE_SEARCH = "https://api.apollo.io/api/v1/mixed_people/search"

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


def _apollo_post(api_key: str, payload: dict) -> dict:
    """POST to Apollo people search with key in header (required by Apollo)."""
    resp = requests.post(
        APOLLO_PEOPLE_SEARCH,
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


def _parse_people(data: dict) -> list[dict]:
    contacts = []
    for person in data.get("people", []):
        phones = person.get("phone_numbers") or []
        phone  = next((p["sanitized_number"] for p in phones if p.get("sanitized_number")), "")
        contacts.append({
            "contact_name":  person.get("name") or "",
            "contact_title": person.get("title") or "",
            "contact_email": person.get("email") or "",
            "contact_phone": phone,
            "linkedin_url":  person.get("linkedin_url") or "",
        })
    return contacts


def search_contacts(
    api_key: str,
    domain: str,
    titles: list[str] | None = None,
    max_results: int = 5,
) -> list[dict]:
    """Search Apollo for contacts at a given company domain."""
    payload: dict = {"q_organization_domains": domain, "page": 1, "per_page": max_results}
    if titles:
        payload["person_titles"] = titles
    return _parse_people(_apollo_post(api_key, payload))


def search_contacts_by_name(
    api_key: str,
    company_name: str,
    titles: list[str] | None = None,
    max_results: int = 5,
) -> list[dict]:
    """Search Apollo by company keyword — used for manually entered company names."""
    payload: dict = {
        "q_keywords": f"{company_name} Singapore",
        "page": 1,
        "per_page": max_results,
    }
    if titles:
        payload["person_titles"] = titles
    return _parse_people(_apollo_post(api_key, payload))


def enrich_leads(
    api_key: str,
    leads_df,
    brand_filter: list[str] | None = None,
    titles: list[str] | None = None,
    max_per_company: int = 3,
    progress_callback=None,
    use_keyword: bool = False,
):
    """
    Fetch contacts for each company in leads_df via Apollo.
    Returns a pd.DataFrame with one row per contact (multiple per company).
    progress_callback(i, total, brand) is called before each company fetch.
    """
    import pandas as pd

    targets = leads_df if brand_filter is None else leads_df[leads_df["Brand"].isin(brand_filter)]
    targets = targets.drop_duplicates(subset=["Brand"])
    total = len(targets)
    rows = []

    for i, (_, row) in enumerate(targets.iterrows()):
        brand = row.get("Brand", "")
        if progress_callback:
            progress_callback(i, total, brand)

        domain = extract_domain(str(row.get("Website", "")))
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

        try:
            if use_keyword:
                contacts = search_contacts_by_name(api_key, brand, titles, max_per_company)
            else:
                domain = extract_domain(str(row.get("Website", "")))
                if not domain:
                    rows.append({**base, **_empty_contact("[No website — skipped]")})
                    continue
                contacts = search_contacts(api_key, domain, titles, max_per_company)
            if not contacts:
                rows.append({**base, **_empty_contact("[No contacts found in Apollo]")})
            else:
                for c in contacts:
                    rows.append({
                        **base,
                        "Contact Name":  c["contact_name"],
                        "Contact Title": c["contact_title"],
                        "Email":         c["contact_email"],
                        "Phone":         c["contact_phone"],
                        "LinkedIn":      c["linkedin_url"],
                    })
        except requests.HTTPError as exc:
            rows.append({**base, **_empty_contact(f"[Apollo error: {exc}]")})
        except Exception as exc:
            rows.append({**base, **_empty_contact(f"[Error: {exc}]")})

    return pd.DataFrame(rows) if rows else pd.DataFrame()


def _empty_contact(note: str) -> dict:
    return {
        "Contact Name":  note,
        "Contact Title": "",
        "Email":         "",
        "Phone":         "",
        "LinkedIn":      "",
    }
