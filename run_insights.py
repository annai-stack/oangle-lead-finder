#!/usr/bin/env python3
"""
AI Lead Insights — end-to-end runner (smoke-test friendly).

Pipeline:
  1. Companies   — from an existing account-leads CSV, or a fresh web search.
  2. Contacts    — Apollo (and optional Hunter) decision-makers per company.
  3. Enrichment  — company-level + contact-level deep enrichment via Claude.
  4. Export      — formatted .xlsx mirroring the reference 'AI Lead Insights'.

Reads API keys from .streamlit/secrets.toml (or the environment).

Examples:
  # Verify Apollo wiring only — one raw call, dump the JSON, no enrichment:
  python run_insights.py --apollo-probe mcdonalds.com.sg

  # Tiny smoke test — 3 companies, 1 contact each, enrich at most 2:
  python run_insights.py --from-latest-csv --max-companies 3 \
      --contacts-per-company 1 --max-enrich 2

  # Full smoke test — 10 companies, up to 3 contacts each:
  python run_insights.py --from-latest-csv --max-companies 10 \
      --contacts-per-company 3
"""

import argparse
import json
import os
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).parent


# ---------------------------------------------------------------------------
# Key loading
# ---------------------------------------------------------------------------

def load_secrets() -> dict:
    """Load keys from .streamlit/secrets.toml, falling back to the environment."""
    keys = {k: os.environ.get(k, "") for k in
            ("ANTHROPIC_API_KEY", "APOLLO_API_KEY", "HUNTER_API_KEY", "BUILTWITH_API_KEY",
             "SUPABASE_URL", "SUPABASE_KEY")}
    secrets_path = HERE / ".streamlit" / "secrets.toml"
    if secrets_path.exists():
        data = tomllib.load(open(secrets_path, "rb"))
        for k in keys:
            if not keys[k] and data.get(k):
                keys[k] = data[k]
    return keys


# ---------------------------------------------------------------------------
# Company sourcing
# ---------------------------------------------------------------------------

def contact_matches(c: dict, title_kw: list[str], sen_kw: list[str]) -> bool:
    """Keep a contact only if its title and seniority match the requested filters.
    Empty filter lists pass everything."""
    if title_kw:
        title = (c.get("contact_title") or c.get("job_title") or "").lower()
        if not any(k in title for k in title_kw):
            return False
    if sen_kw:
        sen = (c.get("seniority") or "").lower()
        if not any(k in sen for k in sen_kw):
            return False
    return True


def companies_from_csv(path: Path, limit: int) -> list[dict]:
    import pandas as pd
    df = pd.read_csv(path)
    out = []
    for _, r in df.iterrows():
        row = r.to_dict()
        out.append({
            "company_name":   str(r.get("company_name") or r.get("brand_name") or ""),
            "brand_name":     str(r.get("brand_name") or ""),
            "website":        str(r.get("website") or ""),
            "segment":        str(r.get("segment") or ""),
            "fit_score":      r.get("fit_score", ""),
            # Account-context fields carried into the insights output (if present).
            "num_outlets_sg":     row.get("num_outlets_sg", ""),
            "fit_reason":         row.get("fit_reason", ""),
            "suggested_products": row.get("suggested_products", ""),
            "grant_eligible":     row.get("grant_eligible", ""),
            "warm_intro":         row.get("warm_intro", ""),
            "source_url":         row.get("source_url", ""),
            "confidence":         row.get("confidence", ""),
        })
        if len(out) >= limit:
            break
    return out


def companies_from_search(limit: int) -> list[dict]:
    import anthropic
    import lead_finder as lf
    client = anthropic.Anthropic()
    leads: list[dict] = []
    for seg in lf.TARGET_SEGMENTS:
        print(f"  → searching: {seg}")
        leads.extend(lf.search_segment(client, seg))
        if len(lf.post_process(list(leads))) >= limit:
            break
    return lf.post_process(leads)[:limit]


# ---------------------------------------------------------------------------
# Apollo probe (verification step 1)
# ---------------------------------------------------------------------------

def apollo_probe(domain: str, apollo_key: str, titles: list[str]) -> None:
    import apollo_client
    print(f"Probing Apollo for domain: {domain}\n")
    raw = apollo_client._apollo_post(apollo_key, {
        "q_organization_domains": domain, "page": 1, "per_page": 2,
        "person_titles": titles,
    })
    people = raw.get("contacts") or raw.get("people") or []
    print(f"Returned {len(people)} person record(s).\n")
    if people:
        print("=== RAW first person (keys available from Apollo) ===")
        print(json.dumps(people[0], indent=2, default=str)[:4000])
        print("\n=== After _parse_apollo (what the pipeline keeps) ===")
        print(json.dumps(apollo_client._parse_apollo(raw)[0], indent=2, default=str))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="AI Lead Insights runner")
    ap.add_argument("--client", default="oangle")
    ap.add_argument("--from-csv", type=Path, help="Source companies from this CSV")
    ap.add_argument("--from-latest-csv", action="store_true",
                    help="Source companies from the newest leads_fnb_*.csv")
    ap.add_argument("--max-companies", type=int, default=10)
    ap.add_argument("--contacts-per-company", type=int, default=3)
    ap.add_argument("--max-enrich", type=int, default=0,
                    help="Cap total contacts enriched (0 = no cap). Use for cheap smoke tests.")
    ap.add_argument("--source", choices=["apollo", "hunter", "both"], default="hunter",
                    help="Contact source. Hunter returns real emails/LinkedIn; Apollo "
                         "search masks PII on lower tiers. Apollo org-enrich is always "
                         "used for firmographics regardless.")
    ap.add_argument("--titles", default="",
                    help="Comma-separated job-title keywords — only contacts whose title "
                         "contains one of these are kept (e.g. 'owner,operations,IT,director'). "
                         "Overrides the client profile's target titles. Empty = profile default.")
    ap.add_argument("--seniority", default="",
                    help="Comma-separated seniority keywords — only contacts whose seniority "
                         "matches are kept (Apollo: owner,c_suite,vp,head,director,manager,senior; "
                         "Hunter: executive,senior,junior). Empty = no seniority filter.")
    ap.add_argument("--apollo-probe", metavar="DOMAIN",
                    help="Run one raw Apollo call against DOMAIN and exit (no enrichment).")
    ap.add_argument("--out", type=Path, help="Output .xlsx path")
    # --- Cost levers ---
    ap.add_argument("--model", default="claude-sonnet-4-6",
                    help="Draft model for the bulk enrichment (cheaper = lower cost).")
    ap.add_argument("--premium-model", default="claude-opus-4-8",
                    help="Model used to polish top-tier outreach (no web search).")
    ap.add_argument("--premium-heat", default="1A",
                    help="Comma-separated heat ranks that get the premium polish (default 1A "
                         "only — use '1A,1B' to polish more, empty for none).")
    ap.add_argument("--no-builtwith", action="store_true",
                    help="Skip BuiltWith technographics even if a key is present.")
    ap.add_argument("--batch", action="store_true",
                    help="Run enrichment via the Batches API (50%% cheaper tokens, "
                         "async — best for large non-urgent runs).")
    # --- Verification (quality gate) ---
    ap.add_argument("--no-verify", action="store_true",
                    help="Skip the verification pass (email validation, source-URL "
                         "resolution, claim cross-check). Verification is ON by default.")
    ap.add_argument("--no-verify-claims", action="store_true",
                    help="Within verification, skip the per-company model claim "
                         "cross-check (still validates emails + source URLs).")
    # --- Persistence / caching (Supabase, optional) ---
    ap.add_argument("--no-db", action="store_true",
                    help="Disable Supabase persistence/caching even if configured.")
    ap.add_argument("--fresh", action="store_true",
                    help="Bypass enrichment-cache READS (regenerate + refresh the cache). "
                         "Use after changing prompts or to force up-to-date research.")
    ap.add_argument("--include-contacted", action="store_true",
                    help="Do not skip contacts already logged as contacted in the DB "
                         "(e.g. for a deliberate follow-up campaign).")
    args = ap.parse_args()

    keys = load_secrets()
    if keys["ANTHROPIC_API_KEY"]:
        os.environ["ANTHROPIC_API_KEY"] = keys["ANTHROPIC_API_KEY"]

    import client_config
    profile = client_config.get_client(args.client)

    # --- Persistence / caching store (no-op NullStore when unconfigured) ---
    import db
    import insights_enrich as ie
    store = db.get_store(keys, client=args.client, prompt_version=ie.PROMPT_VERSION,
                         fresh=args.fresh, disabled=args.no_db)
    if store.enabled:
        store.start_run({k: str(v) for k, v in vars(args).items()})
        print(f"      DB: connected · cache {'BYPASS (--fresh)' if args.fresh else 'on'} · "
              f"{len(store._contacted)} contact(s) suppressed from prior outreach")

    # --- Verification mode: Apollo probe only ---
    if args.apollo_probe:
        if not keys["APOLLO_API_KEY"]:
            sys.exit("APOLLO_API_KEY is empty — paste it into .streamlit/secrets.toml first.")
        apollo_probe(args.apollo_probe, keys["APOLLO_API_KEY"], profile["target_titles"])
        return

    # --- Preflight ---
    if not keys["ANTHROPIC_API_KEY"]:
        sys.exit("ANTHROPIC_API_KEY is empty.")
    if args.source in ("apollo", "both") and not keys["APOLLO_API_KEY"]:
        sys.exit("APOLLO_API_KEY is empty — paste it into .streamlit/secrets.toml first.")
    if args.source in ("hunter", "both") and not keys["HUNTER_API_KEY"]:
        sys.exit("HUNTER_API_KEY is empty — paste it into .streamlit/secrets.toml first.")

    # --- Stage 1: companies ---
    print(f"[1/4] Sourcing up to {args.max_companies} companies …")
    if args.from_latest_csv:
        csvs = sorted(HERE.glob("leads_fnb_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not csvs:
            sys.exit("No leads_fnb_*.csv found — run an account search first or use --from-csv.")
        companies = companies_from_csv(csvs[0], args.max_companies)
        print(f"      from {csvs[0].name}")
    elif args.from_csv:
        companies = companies_from_csv(args.from_csv, args.max_companies)
    else:
        companies = companies_from_search(args.max_companies)
    print(f"      {len(companies)} companies")

    # --- Stage 2: contacts ---
    import apollo_client
    # Title / seniority filters — only contacts matching these reach the output.
    raw_titles  = [t.strip() for t in args.titles.split(",") if t.strip()]
    title_kw    = [t.lower() for t in raw_titles]
    sen_kw      = [s.strip().lower() for s in args.seniority.split(",") if s.strip()]
    apollo_titles = raw_titles or profile["target_titles"]
    filtering   = bool(title_kw or sen_kw)
    # When filtering, over-fetch so we still net enough matching contacts.
    fetch_n = max(args.contacts_per_company * 3, 10) if filtering else args.contacts_per_company

    print(f"[2/4] Fetching contacts (≤{args.contacts_per_company}/company, source={args.source}) …")
    if filtering:
        print(f"      filter — titles: {title_kw or '(any)'}  |  seniority: {sen_kw or '(any)'}")
    pairs: list[tuple[dict, dict]] = []   # (company, contact)
    enrich_budget = args.max_enrich or 10**9
    skipped_contacted = 0                 # leads dropped by outreach dedup
    for co in companies:
        domain = apollo_client.extract_domain(co.get("website", ""))

        # Firmographics: one Apollo org-enrich call per company → merge into co.
        # Cached by domain so reruns don't re-spend Apollo credits.
        if domain and keys["APOLLO_API_KEY"]:
            firmo = store.get_org(domain)
            if firmo is None:
                firmo = apollo_client.enrich_organization_apollo(keys["APOLLO_API_KEY"], domain)
                store.put_org(domain, firmo)
            for k, v in (firmo or {}).items():
                if v and not co.get(k):
                    co[k] = v

        # Technographics (optional): BuiltWith competitor/greenfield signal.
        if domain and keys["BUILTWITH_API_KEY"] and not args.no_builtwith:
            import builtwith_client
            sig = builtwith_client.tech_signals_summary(keys["BUILTWITH_API_KEY"], domain)
            if sig:
                co["tech_signals"] = sig
                print(f"      {co.get('company_name','?')[:26]:28} tech: {sig[:60]}")

        contacts: list[dict] = []
        try:
            if args.source in ("hunter", "both") and domain and keys["HUNTER_API_KEY"]:
                contacts += apollo_client.search_contacts_hunter(
                    keys["HUNTER_API_KEY"], domain, fetch_n)
            # Apollo as primary (if chosen) or as fallback when Hunter found nothing.
            need_apollo = args.source in ("apollo", "both") or (
                args.source == "hunter" and not contacts)
            if need_apollo and keys["APOLLO_API_KEY"]:
                if domain:
                    contacts += apollo_client.search_contacts_apollo(
                        keys["APOLLO_API_KEY"], domain, apollo_titles, fetch_n)
                else:
                    contacts += apollo_client.search_contacts_apollo_by_name(
                        keys["APOLLO_API_KEY"], co.get("company_name", ""),
                        apollo_titles, fetch_n)
        except Exception as exc:
            print(f"      [WARN] contact fetch failed for {co.get('company_name')}: {exc}")

        # Title / seniority filter — keep only contacts in the requested roles.
        if filtering:
            kept = [c for c in contacts if contact_matches(c, title_kw, sen_kw)]
            dropped = len(contacts) - len(kept)
            contacts = kept
            if dropped:
                print(f"      {co.get('company_name','?')[:26]:28} filtered out {dropped} off-role contact(s)")

        # Outreach dedup — drop contacts already worked in a prior run, so we
        # spend zero enrichment on them. Skip with --include-contacted.
        if store.enabled and not args.include_contacted:
            before = len(contacts)
            contacts = [c for c in contacts
                        if not store.already_contacted(c.get("contact_email") or c.get("email"))]
            skipped_contacted += before - len(contacts)

        contacts = contacts[:args.contacts_per_company]
        print(f"      {co.get('company_name','?'):<32} {len(contacts)} contact(s)")
        for c in contacts:
            if len(pairs) >= enrich_budget:
                break
            pairs.append((co, c))
        if len(pairs) >= enrich_budget:
            print(f"      (reached --max-enrich={args.max_enrich}, stopping contact fetch)")
            break

    if skipped_contacted:
        print(f"      (skipped {skipped_contacted} already-contacted lead(s) — "
              f"--include-contacted to re-include)")

    if not pairs:
        sys.exit("No contacts found — check Apollo plan/credits or try --source both.")

    # --- Stage 3: enrichment ---
    import anthropic
    import insights_enrich as ie
    import cost_model
    client = anthropic.Anthropic()
    draft_model = args.model
    premium_model = args.premium_model
    premium_ranks = {r.strip() for r in args.premium_heat.split(",") if r.strip()}
    mode = "BATCH (async, 50% off)" if args.batch else "sync"
    print(f"[3/4] Enriching {len(pairs)} contact(s) across "
          f"{len({id(c) for c, _ in pairs})} company record(s) · {mode} …")
    print(f"      draft model: {draft_model}  |  premium polish: {premium_model} "
          f"for heat {sorted(premium_ranks) or '(none)'}")

    if args.batch:
        import batch_runner
        records, stages = batch_runner.enrich_batch(
            client, profile, pairs, draft_model, premium_model, premium_ranks)
    else:
        co_usage      = cost_model.blank_usage()  # Stage 3a: company-level (draft model)
        contact_usage = cost_model.blank_usage()  # Stage 3b: contact-level (draft model)
        polish_usage  = cost_model.blank_usage()  # Stage 3c: premium polish (premium model)
        company_cache: dict[str, dict] = {}
        records = []
        for i, (co, contact) in enumerate(pairs, 1):
            ckey = co.get("company_name", "") or co.get("website", "")
            if ckey not in company_cache:
                print(f"      [{i}/{len(pairs)}] company enrich: {ckey}")
                company_cache[ckey] = ie.enrich_company(
                    client, profile, co, usage_acc=co_usage, model=draft_model, cache=store)
            name = contact.get("contact_name") or contact.get("first_name", "?")
            print(f"      [{i}/{len(pairs)}] contact enrich: {name}")
            draft = ie.enrich_contact(
                client, profile, {**co, **company_cache[ckey]}, contact,
                usage_acc=contact_usage, model=draft_model, cache=store)
            # Two-tier: polish only high heat-rank leads on the premium model.
            if draft.get("heat_rank") in premium_ranks:
                print(f"            ↑ premium polish ({draft['heat_rank']})")
                draft = ie.polish_outreach(client, profile, {**co, **company_cache[ckey]},
                                           contact, draft, model=premium_model,
                                           usage_acc=polish_usage, cache=store)
            records.append(ie.build_record(profile, co, contact, company_cache[ckey], draft))
        stages = {
            "company enrich": (co_usage, draft_model),
            "contact enrich": (contact_usage, draft_model),
            "premium polish": (polish_usage, premium_model),
        }

    # Per-stage token-fee breakdown (each stage priced at its own model; batch = 50% off).
    print("\n=== Token-Fee Breakdown ===")
    print(f"{'Stage':<26}{'model':<20}{'in':>9}{'out':>8}{'srch':>6}{'$':>9}")
    grand = 0.0
    for label, (usage, m) in stages.items():
        c = cost_model.cost(usage, m, batch=args.batch)
        grand += c["total"]
        print(f"{label:<26}{m:<20}{usage['input_tokens']:>9,}{usage['output_tokens']:>8,}"
              f"{usage['web_search_requests']:>6}{c['total']:>9.4f}")
    n = len(records) or 1
    print("-" * 78)
    print(f"{'TOTAL':<46}{'':>9}{'':>8}{'':>6}{grand:>9.4f}")
    print(f"      Per-lead average: ${grand / n:.4f}  ·  {n} leads"
          + ("  (batch 50% applied)" if args.batch else ""))

    # --- Stage 3.5: verification (quality gate) ---
    # Turns the model's self-reported claims into checkable signals: validates
    # emails (Hunter), resolves the discovery source URL, and cross-checks the
    # asserted company/outlet facts against that page. Path-agnostic — runs on
    # records from either the sync or the batch path.
    if not args.no_verify:
        import verify
        verify_usage = cost_model.blank_usage()
        check_claims = not args.no_verify_claims
        verify_emails = bool(keys["HUNTER_API_KEY"])
        print(f"[3.5] Verifying {len(records)} record(s) · "
              f"emails={'on' if verify_emails else 'off (no Hunter key)'} · "
              f"claims={'on' if check_claims else 'off'} …")
        vs = verify.verify_records(
            client, records, keys, model=draft_model,
            check_claims=check_claims, verify_emails=verify_emails,
            usage_acc=verify_usage)
        print(f"      email:  {vs['deliverable']} deliverable · {vs['risky']} risky · "
              f"{vs['undeliverable']} undeliverable · {vs['email_unknown']} unknown")
        print(f"      data:   {vs['high']} high · {vs['med']} med · {vs['low']} low confidence")
        print(f"      → {vs['needs_review']} row(s) flagged for review")
        if check_claims:
            vc = cost_model.cost(verify_usage, draft_model, batch=False)
            print(f"      claim-check cost: ${vc['total']:.4f}")

    # --- Stage 4: export ---
    import excel_export
    out = str(args.out) if args.out else None
    path = excel_export.write_xlsx(records, profile, filename=out)
    print(f"[4/4] Wrote {len(records)} row(s) → {path}")

    # Refresh the Streamlit viewer's data source so localhost reflects this run.
    records_json = HERE / "_insights_records.json"
    records_json.write_text(json.dumps(records, indent=2, default=str))
    print(f"      viewer data refreshed → {records_json.name} ({len(records)} record(s))")

    # --- Persist + log outreach so future runs cache/dedup ---
    if store.enabled:
        logged = 0
        for rec in records:
            cid = store.persist_record(rec)
            if cid:
                store.log_outreach(cid, status="exported")
                logged += 1
        store.finish_run(n_companies=len({id(c) for c, _ in pairs}),
                         n_contacts=len(records), cost_usd=grand)
        st = store.cache_stats()
        print(f"      DB: persisted {len(records)} contact(s) · logged {logged} outreach row(s) · "
              f"enrichment cache hits {st['hits']} / misses {st['misses']}")


if __name__ == "__main__":
    main()
