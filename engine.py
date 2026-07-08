"""Orchestration entry points for the two Lead-Finder UI modes.

Importable (no CLI, no argparse) so the API service can call them with a
progress callback. Reuses discover.py + apollo_client + insights_enrich.

  find_accounts(...) -> account-level records   (deck "Tier 1" table)
  find_leads(...)    -> contact-level records   (deck "Tier 2" cards)

Both accept two input shapes:
  * industries + market (+ size band)  -> discover accounts, then process
  * a single company (name + url)      -> process just that company
"""

import anthropic

import apollo_client
import cost_model
import discover
import insights_enrich as ie


def _noop(_):  # default progress sink
    pass


def _domain(company: dict) -> str:
    return apollo_client.extract_domain(
        company.get("website") or company.get("company_domain") or "")


def _firmographics(keys: dict, company: dict) -> None:
    """Merge Apollo org-enrich firmographics into `company` in place."""
    domain = _domain(company)
    if domain and keys.get("APOLLO_API_KEY"):
        try:
            firmo = apollo_client.enrich_organization_apollo(keys["APOLLO_API_KEY"], domain)
            for k, v in (firmo or {}).items():
                if v and not company.get(k):
                    company[k] = v
        except Exception:
            pass


def _tech_signal(keys: dict, company: dict, use_builtwith: bool) -> None:
    domain = _domain(company)
    if use_builtwith and domain and keys.get("BUILTWITH_API_KEY"):
        try:
            import builtwith_client
            sig = builtwith_client.tech_signals_summary(keys["BUILTWITH_API_KEY"], domain)
            if sig:
                company["tech_signals"] = sig
        except Exception:
            pass


def _fetch_contacts(keys: dict, company: dict, titles: list[str], n: int,
                    source: str = "hunter") -> list[dict]:
    domain = _domain(company)
    contacts: list[dict] = []
    try:
        if source in ("hunter", "both") and domain and keys.get("HUNTER_API_KEY"):
            contacts += apollo_client.search_contacts_hunter(keys["HUNTER_API_KEY"], domain, n)
        need_apollo = source in ("apollo", "both") or (source == "hunter" and not contacts)
        if need_apollo and keys.get("APOLLO_API_KEY"):
            if domain:
                contacts += apollo_client.search_contacts_apollo(
                    keys["APOLLO_API_KEY"], domain, titles, n)
            else:
                contacts += apollo_client.search_contacts_apollo_by_name(
                    keys["APOLLO_API_KEY"], company.get("company_name", ""), titles, n)
    except Exception:
        pass
    return contacts


# ---------------------------------------------------------------------------
# Mode 1 — Find Accounts  (account-level intelligence, deck Tier 1)
# ---------------------------------------------------------------------------

def find_accounts(client: anthropic.Anthropic, keys: dict, profile: dict, *,
                  industries: list[str] | None = None, market: str = "",
                  size_band: str = "", company_name: str = "", url: str = "",
                  max_accounts: int = 8, use_builtwith: bool = True,
                  on_event=_noop) -> dict:
    """Return {"accounts": [...], "cost": {...}}. Accounts carry the deck's
    Tier-1 fields: sector/size, priority band, why-it-scores, likely use case,
    how-to-target, and up to 3 named champions (name + title only)."""
    usage = cost_model.blank_usage()

    if company_name or url:
        accounts = [{"company_name": company_name or url, "website": url,
                     "sector": "", "market": market}]
        on_event({"stage": "discover", "msg": f"profiling {company_name or url}"})
    else:
        accounts = discover.discover_accounts(
            client, profile, industries or [], market, size_band, max_accounts,
            usage_acc=usage, on_event=on_event)
    on_event({"stage": "discovered", "count": len(accounts)})

    out = []
    for i, acc in enumerate(accounts, 1):
        name = acc.get("company_name", "?")
        on_event({"stage": "enrich", "i": i, "n": len(accounts), "msg": f"enriching {name}"})
        _firmographics(keys, acc)
        _tech_signal(keys, acc, use_builtwith)
        enr = ie.enrich_company(client, profile, acc, usage_acc=usage, model=discover.MODEL)
        champs = _fetch_contacts(keys, acc, profile["target_titles"], 3)
        champions = [{"name": (c.get("contact_name")
                               or f"{c.get('first_name','')} {c.get('last_name','')}".strip()),
                      "title": c.get("contact_title") or c.get("job_title") or ""}
                     for c in champs[:3]]
        out.append({
            "company_name":   name,
            "website":        acc.get("website", ""),
            "sector":         acc.get("sector") or acc.get("industry", ""),
            "company_size":   acc.get("company_size", ""),
            "location":       acc.get("location", ""),
            "market":         acc.get("market", market),
            "priority_band":  acc.get("priority_band", ""),
            "why_it_scores":  acc.get("why_it_scores", ""),
            "likely_use_case": acc.get("likely_use_case", ""),
            "how_to_target":  enr.get("how_to_target", ""),
            "company_topline": enr.get("company_topline", ""),
            "company_press":  enr.get("company_press", ""),
            "champions":      champions,
            "tech_signals":   acc.get("tech_signals", ""),
            "confidence":     acc.get("confidence", ""),
            "source_url":     acc.get("source_url", ""),
        })
        on_event({"stage": "account", "record": out[-1]})

    return {"accounts": out,
            "cost": cost_model.cost(usage, discover.MODEL, batch=False)}


# ---------------------------------------------------------------------------
# Mode 2 — Find Leads  (contact-level intelligence, deck Tier 2)
# ---------------------------------------------------------------------------

def find_leads(client: anthropic.Anthropic, keys: dict, profile: dict, *,
               industries: list[str] | None = None, market: str = "",
               size_band: str = "", company_name: str = "", url: str = "",
               titles: list[str] | None = None, max_accounts: int = 5,
               contacts_per_company: int = 3, use_builtwith: bool = True,
               on_event=_noop) -> dict:
    """Return {"leads": [...], "cost": {...}}. Each lead is a per-contact record
    (build_record) with verified-contact fields + company context + heat rank."""
    usage = cost_model.blank_usage()
    title_kw = [t.lower() for t in (titles or [])]
    apollo_titles = titles or profile["target_titles"]

    if company_name or url:
        companies = [{"company_name": company_name or url, "website": url, "market": market}]
        on_event({"stage": "discover", "msg": f"profiling {company_name or url}"})
    else:
        companies = discover.discover_accounts(
            client, profile, industries or [], market, size_band, max_accounts,
            usage_acc=usage, on_event=on_event)
    on_event({"stage": "discovered", "count": len(companies)})

    leads = []
    for ci, co in enumerate(companies, 1):
        name = co.get("company_name", "?")
        _firmographics(keys, co)
        _tech_signal(keys, co, use_builtwith)
        contacts = _fetch_contacts(keys, co, apollo_titles, max(contacts_per_company * 2, 6))
        if title_kw:
            contacts = [c for c in contacts
                        if any(k in (c.get("contact_title") or c.get("job_title") or "").lower()
                               for k in title_kw)]
        contacts = contacts[:contacts_per_company]
        on_event({"stage": "contacts", "company": name, "count": len(contacts)})
        if not contacts:
            continue
        comp_enr = ie.enrich_company(client, profile, co, usage_acc=usage, model=discover.MODEL)
        merged = {**co, **comp_enr}
        for contact in contacts:
            cname = (contact.get("contact_name")
                     or f"{contact.get('first_name','')} {contact.get('last_name','')}".strip())
            on_event({"stage": "enrich", "msg": f"enriching {cname} @ {name}"})
            cont_enr = ie.enrich_contact(client, profile, merged, contact,
                                         usage_acc=usage, model=discover.MODEL)
            rec = ie.build_record(profile, co, contact, comp_enr, cont_enr)
            leads.append(rec)
            on_event({"stage": "lead", "record": rec})

    return {"leads": leads,
            "cost": cost_model.cost(usage, discover.MODEL, batch=False)}
