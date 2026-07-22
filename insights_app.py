"""
AI Lead Insights — local viewer (Streamlit).

Serves the enriched lead records produced by the Option-A pipeline as an
interactive page: heat-rank metrics, filterable table, expandable lead cards,
and an Excel download. Run with:

    .venv/bin/streamlit run insights_app.py

Note: this view DISPLAYS pre-generated insights (no Anthropic key needed). To
regenerate the AI columns, run the enrichment pipeline once Anthropic credits
are available, then refresh this page.
"""

import io
import json
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

import client_config
import excel_export
import apollo_client
import builtwith_client
import anthropic
import insights_enrich as ie
import verify
import db
import lead_finder as lf

HERE = Path(__file__).parent
RECORDS = HERE / "_insights_records.json"


def _secrets() -> dict:
    """Read API keys from Streamlit secrets (.streamlit/secrets.toml)."""
    keys = {}
    for k in ("ANTHROPIC_API_KEY", "APOLLO_API_KEY", "HUNTER_API_KEY", "BUILTWITH_API_KEY",
              "SUPABASE_URL", "SUPABASE_KEY"):
        keys[k] = st.secrets.get(k, "") if hasattr(st, "secrets") else ""
    return keys


def load_previous_leads(keys: dict):
    """Every lead ever persisted to Supabase (newest first).
    Returns (records, error_message_or_None)."""
    store = db.get_store(keys, client="oangle", prompt_version=ie.PROMPT_VERSION)
    if not getattr(store, "enabled", False):
        return [], "Supabase not configured"
    try:
        res = (store.sb.table("contacts").select("data, created_at")
               .eq("client", "oangle").order("created_at", desc=True).limit(1000).execute())
        out = [r["data"] for r in (res.data or [])
               if isinstance(r.get("data"), dict) and r["data"].get("company_name")]
        return out, None
    except Exception as e:
        return [], str(e)[:160]


KEYS = _secrets()
# BuiltWith key (optional) — enables a live greenfield/commerce technographic
# signal per company. Read from Streamlit secrets; degrades silently if absent.
_BW_KEY = KEYS["BUILTWITH_API_KEY"]


def enrich_single_company(profile: dict, company: dict, keys: dict, n_contacts: int = 2):
    """Run the live pipeline for ONE user-entered company → (records, error_or_None).

    Mirrors the run_insights flow for a single company: Apollo firmographics →
    contacts (Hunter, Apollo fallback) → company + contact enrichment → premium
    polish on 1A → verification.
    """
    if not keys.get("ANTHROPIC_API_KEY"):
        return [], "ANTHROPIC_API_KEY missing — add it to .streamlit/secrets.toml."
    orig_site = company.get("website", "")
    client = anthropic.Anthropic(api_key=keys["ANTHROPIC_API_KEY"])
    store = db.get_store(keys, client="oangle", prompt_version=ie.PROMPT_VERSION)
    if store.enabled:
        store.start_run({"source": "viewer", "company": company.get("company_name", "")})

    # Account scoring — fills Outlets / Fit score / Suggested products / Grant flag
    # (the fields the segment-discovery stage normally produces) and the official
    # website it finds on the web.
    score = lf.score_company(client, company.get("company_name", ""),
                             orig_site, company.get("segment", ""))
    for k in ("num_outlets_sg", "fit_score", "fit_reason", "confidence",
              "grant_eligible", "suggested_products", "segment"):
        v = score.get(k)
        if v not in (None, "", []) and not company.get(k):
            company[k] = v
    # Verify / correct the URL before any contact lookup — Hunter & Apollo are
    # domain-keyed, so the right domain is what makes or breaks the search.
    resolved_url, url_note = lf.resolve_url(orig_site, score.get("official_website", ""))
    if resolved_url:
        company["website"] = resolved_url
    domain = apollo_client.extract_domain(company.get("website", ""))
    if domain and domain != apollo_client.extract_domain(orig_site):
        st.info(f"🔗 Using verified website **{domain}** ({url_note}) — "
                f"the entered URL didn't resolve.")
    brand = (company.get("company_name") or "").lower()
    company["warm_intro"] = "Y" if any(w in brand for w in lf.WARM_INTRO_BRANDS) else "N"

    # Firmographics
    if domain and keys.get("APOLLO_API_KEY"):
        firmo = apollo_client.enrich_organization_apollo(keys["APOLLO_API_KEY"], domain)
        for k, v in (firmo or {}).items():
            if v and not company.get(k):
                company[k] = v
    # Tech signal (optional)
    if domain and keys.get("BUILTWITH_API_KEY"):
        sig = builtwith_client.tech_signals_summary(keys["BUILTWITH_API_KEY"], domain)
        if sig:
            company["tech_signals"] = sig

    # Contacts — Hunter first, Apollo fallback
    contacts = []
    try:
        if domain and keys.get("HUNTER_API_KEY"):
            contacts += apollo_client.search_contacts_hunter(keys["HUNTER_API_KEY"], domain, n_contacts)
        if not contacts and keys.get("APOLLO_API_KEY"):
            if domain:
                contacts += apollo_client.search_contacts_apollo(
                    keys["APOLLO_API_KEY"], domain, profile["target_titles"], n_contacts)
            else:
                contacts += apollo_client.search_contacts_apollo_by_name(
                    keys["APOLLO_API_KEY"], company.get("company_name", ""),
                    profile["target_titles"], n_contacts)
    except Exception as exc:
        return [], f"Contact lookup failed: {exc}"
    # Drop un-actionable contacts (no email AND no LinkedIn) — e.g. masked Apollo
    # by-name results that happen when no website was given.
    contacts = [c for c in contacts
                if (c.get("contact_email") or c.get("email") or c.get("linkedin_url"))]
    contacts = contacts[:n_contacts]
    if not contacts:
        used = f" (searched domain: {domain})" if domain else ""
        msg = ("No reachable contacts found" + used + ". " +
               ("Add the company's website so Hunter can find real emails — "
                "without a domain, results are usually masked." if not domain
                else "This company likely has no named decision-makers in "
                     "Hunter/Apollo — only generic role inboxes, which are skipped."))
        return [], msg

    # Enrich + verify (cache-aware → reruns of the same company are near-free)
    co_enrich = ie.enrich_company(client, profile, company, cache=store)
    records = []
    for contact in contacts:
        draft = ie.enrich_contact(client, profile, {**company, **co_enrich}, contact, cache=store)
        if draft.get("heat_rank") == "1A":
            draft = ie.polish_outreach(client, profile, {**company, **co_enrich}, contact, draft, cache=store)
        records.append(ie.build_record(profile, company, contact, co_enrich, draft))
    try:
        verify.verify_records(client, records, keys, model=ie.DRAFT_MODEL,
                              check_claims=True, verify_emails=bool(keys.get("HUNTER_API_KEY")))
    except Exception:
        pass

    # Persist to Supabase so it shows up under "previously generated leads".
    if store.enabled:
        for rec in records:
            cid = store.persist_record(rec)
            if cid:
                store.log_outreach(cid, status="exported")
        store.finish_run(n_companies=1, n_contacts=len(records))
    return records, None


@st.cache_data(show_spinner=False)
def tech_signal(domain: str) -> str:
    """Live BuiltWith signal for a domain (cached per session)."""
    if not _BW_KEY or not domain:
        return ""
    return builtwith_client.tech_signals_summary(_BW_KEY, domain)

st.set_page_config(page_title="AI Lead Insights", page_icon="🎯", layout="wide")

HEAT_COLORS = {"1A": "#16a34a", "1B": "#2563eb", "2": "#d97706", "3": "#6b7280", "4": "#9ca3af"}
HEAT_DEFS = {
    "1A": "Immediate follow-up — ideal ICP + senior decision-maker",
    "1B": "Strong — established chain + influential role",
    "2":  "Moderate — smaller chain or mid-level contact",
    "3":  "Contextual — borderline fit, nurture",
    "4":  "Low weight — weak fit, deprioritise",
}

st.markdown("""
<style>
  .heat { color:#fff; padding:2px 10px; border-radius:4px; font-weight:700; font-size:0.85em; }
  div[data-testid="stMetricValue"] { font-size:1.8rem; }
</style>
""", unsafe_allow_html=True)

# --- Load data ---
# First launch shows ONLY this session's fresh searches (empty until you search),
# so a demo never opens on old leads. Flip the sidebar toggle to bring in the
# back-catalogue: the curated sample set + every lead ever generated (Supabase).
if "session_records" not in st.session_state:
    st.session_state.session_records = []


def _dedup(rows):
    """Dedup leads by email, falling back to name+company."""
    seen, out = set(), []
    for r in rows:
        key = ((r.get("email") or "").strip().lower()
               or (str(r.get("first_name", "")) + str(r.get("last_name", "")) +
                   str(r.get("company_name", ""))).lower())
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(r)
    return out


show_prev = st.sidebar.toggle(
    "📚 Show previously generated leads", value=False,
    help="Load the back-catalogue — the curated sample set plus every lead ever "
         "generated (Supabase) — instead of just this session's searches.")
profile = client_config.get_client("oangle")
session_recs = st.session_state.session_records
if show_prev:
    prev, err = load_previous_leads(KEYS)
    if err:
        # Supabase is optional and the local records still load, so this is not
        # a user-facing failure — a raw DNS/errno box on screen only looks
        # broken (badly so in a demo). Note it quietly; log the detail.
        print(f"[insights_app] Supabase history unavailable: {err}", file=sys.stderr)
        st.sidebar.caption("Cloud history unavailable — showing local records.")
        prev = []
    local = json.loads(RECORDS.read_text()) if RECORDS.exists() else []
    # Full view = this session ∪ Supabase history ∪ curated sample, deduped.
    records = _dedup(session_recs + prev + local)
    st.sidebar.caption(f"📚 {len(records)} lead(s) in full history")
else:
    records = session_recs
df = pd.DataFrame(records)

st.title("🎯 AI Lead Insights")
st.caption(f"Client: **{profile['client_name']}** · {len(df)} enriched leads · "
           "contacts from Hunter + Apollo, enrichment researched per contact")

def find_in_history(name: str, site: str) -> list:
    """Records already on hand that match the typed company — so a known company
    loads INSTANTLY instead of re-running the ~2-min live pipeline. Searches this
    session + the curated sample file + Supabase history. Matches on domain or a
    name substring (so "KFC" finds "QSR Brands … (KFC Singapore operator)")."""
    q_name = (name or "").strip().lower()
    q_dom = apollo_client.extract_domain(site or "")
    pool = list(st.session_state.session_records)
    pool += json.loads(RECORDS.read_text()) if RECORDS.exists() else []
    prev, _err = load_previous_leads(KEYS)
    pool += prev
    hits = []
    for r in pool:
        r_name = str(r.get("company_name") or r.get("brand_name") or "").strip().lower()
        r_dom = apollo_client.extract_domain(
            r.get("company_domain") or r.get("website") or r.get("email") or "")
        name_match = len(q_name) >= 3 and (q_name in r_name or (r_name and r_name in q_name))
        dom_match = bool(q_dom) and q_dom == r_dom
        if name_match or dom_match:
            hits.append(r)
    return _dedup(hits)


# --- Interactive: find leads for a company the user types in ---
with st.sidebar:
    st.header("➕ Find leads for a company")
    st.caption("Already in history → loads instantly. New company → runs the agent "
               "live (Anthropic + Apollo/Hunter), ~1–2 min.")
    in_name = st.text_input("Company name", placeholder="e.g. KFC")
    in_site = st.text_input("Website", placeholder="e.g. kfc.com.sg")
    in_seg  = st.text_input("Segment (optional)", placeholder="bubble tea")
    in_out  = st.text_input("Outlets in SG (optional)", placeholder="40")
    in_n    = st.number_input("Contacts to pull", min_value=1, max_value=5, value=1,
                              help="1 is fastest for a live demo; raise for more contacts per company.")
    force_fresh = st.checkbox(
        "🔁 Force fresh research",
        help="Off (default): if the company is already in history, load it instantly. "
             "On: always re-run the live pipeline, even for a known company.")
    can_run = bool(KEYS["ANTHROPIC_API_KEY"])
    go = st.button("🔍 Find leads", type="primary", use_container_width=True, disabled=not can_run)
    if not can_run:
        st.warning("Add ANTHROPIC_API_KEY to .streamlit/secrets.toml to enable live runs.")
    if go:
        if not (in_name or in_site):
            st.error("Enter a company name or website.")
        else:
            hits = [] if force_fresh else find_in_history(in_name, in_site)
            if hits:
                # Instant path — surface the existing full-quality records.
                st.session_state.session_records = _dedup(
                    hits + st.session_state.session_records)
                st.success(f"⚡ Loaded {len(hits)} lead(s) for {in_name or in_site} "
                           "from history — instant. (Tick 'Force fresh research' to re-run live.)")
                st.rerun()
            else:
                company = {"company_name": in_name or in_site, "brand_name": in_name,
                           "website": in_site, "segment": in_seg, "num_outlets_sg": in_out}
                with st.spinner(f"Researching {in_name or in_site} live (~1–2 min) …"):
                    try:
                        new_recs, err = enrich_single_company(profile, company, KEYS, int(in_n))
                    except Exception as exc:
                        new_recs, err = [], str(exc)
                if err:
                    st.error(err)
                elif new_recs:
                    # Show fresh results in THIS session only — the curated sample file
                    # stays clean, and these are already persisted to Supabase inside
                    # enrich_single_company (so the toggle's history picks them up too).
                    st.session_state.session_records = _dedup(
                        new_recs + st.session_state.session_records)
                    st.success(f"Added {len(new_recs)} lead(s) for {in_name or in_site}. Refreshing…")
                    st.rerun()

# Empty state — no leads yet (e.g. cleared to start fresh). Keep the sidebar
# search usable; show a prompt instead of crashing on missing columns.
if df.empty or "heat_rank" not in df.columns:
    st.info("No leads loaded yet. Use **➕ Find leads for a company** in the left "
            "sidebar to search for fresh leads — they'll appear here.")
    st.stop()

# --- Metrics ---
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total leads", len(df))
c2.metric("🔥 Hot (1A)", (df["heat_rank"] == "1A").sum())
c3.metric("Strong (1B)", (df["heat_rank"] == "1B").sum())
c4.metric("Companies", df["company_name"].nunique())
c5.metric("With email", (df["email"].str.len() > 0).sum())

# --- Heat-rank legend ---
with st.expander("ℹ️  What do the heat ranks mean?", expanded=False):
    st.caption("Every lead is scored 1A → 4 on ICP/product fit (40%), role & influence "
               "(40%), and reachability/intent (20%). The sheet is sorted hottest-first.")
    for hr in profile["heat_ranks"]:
        color = HEAT_COLORS.get(hr, "#6b7280")
        st.markdown(
            f'<span class="heat" style="background:{color}">Heat {hr}</span> &nbsp; '
            f'{HEAT_DEFS.get(hr, "")}', unsafe_allow_html=True)
st.markdown("---")

# --- Filters ---
fc1, fc2 = st.columns([1, 2])
with fc1:
    heat_f = st.multiselect("Heat rank", profile["heat_ranks"],
                            default=profile["heat_ranks"])
with fc2:
    co_opts = sorted(df["company_name"].unique().tolist())
    co_f = st.multiselect("Company", co_opts, default=co_opts)

fc3, fc4 = st.columns([2, 1])
with fc3:
    title_q = st.text_input(
        "Job-title keywords (comma-separated — keep only matching titles)",
        placeholder="owner, operations, IT, director, procurement, general manager")
with fc4:
    sen_opts = sorted(s for s in df["seniority"].dropna().unique().tolist() if s)
    sen_f = st.multiselect("Seniority", sen_opts, default=sen_opts)

view = df[df["heat_rank"].isin(heat_f) & df["company_name"].isin(co_f)]
if sen_opts:
    view = view[view["seniority"].isin(sen_f)]
title_kw = [t.strip().lower() for t in title_q.split(",") if t.strip()]
if title_kw:
    view = view[view["job_title"].fillna("").str.lower().apply(
        lambda t: any(k in t for k in title_kw))]
st.markdown(f"**{len(view)} leads shown**"
            + (f"  ·  title filter: {title_kw}" if title_kw else ""))

# --- Table (key columns) ---
table_cols = ["heat_rank", "first_name", "last_name", "job_title", "company_name",
              "num_outlets_sg", "fit_score", "suggested_products", "email", "linkedin_url"]
table_cols = [c for c in table_cols if c in view.columns]
st.dataframe(view[table_cols], use_container_width=True, hide_index=True, height=380)

# --- Download full 23-column xlsx ---
buf = io.BytesIO()
tmp = HERE / "_download_tmp.xlsx"
excel_export.write_xlsx(view.to_dict("records"), profile, filename=str(tmp))
buf.write(tmp.read_bytes()); tmp.unlink(); buf.seek(0)
st.download_button("⬇️  Download full AI Lead Insights (.xlsx)", data=buf.getvalue(),
                   file_name="AI_Lead_Insights_Oangle.xlsx",
                   mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                   use_container_width=True)

st.markdown("---")
st.subheader("📋 Lead Cards")
for _, r in view.iterrows():
    color = HEAT_COLORS.get(r["heat_rank"], "#6b7280")
    name = f"{r['first_name']} {r['last_name']}".strip()
    with st.expander(f"{name} — {r['job_title']} · {r['company_name']}  ·  Heat {r['heat_rank']}"):
        st.markdown(f'<span class="heat" style="background:{color}">Heat {r["heat_rank"]}</span>',
                    unsafe_allow_html=True)
        a, b = st.columns(2)
        with a:
            st.markdown(f"**Email:** {r['email']}")
            st.markdown(f"**Seniority:** {r.get('seniority','')}")
            st.markdown(f"**Company size:** {r.get('company_size','')}  ·  **Industry:** {r.get('industry','')}")
            if r.get("linkedin_url"):
                st.markdown(f"**LinkedIn:** [{r['linkedin_url']}]({r['linkedin_url']})")
        with b:
            st.markdown(f"**Company:** {r['company_name']}")
            st.markdown(f"**Location:** {r.get('location','')}")
            # Live BuiltWith technographic signal (greenfield vs has-commerce).
            domain = apollo_client.extract_domain(r.get("company_domain") or r.get("email", ""))
            sig = tech_signal(domain)
            if sig:
                greenfield = "greenfield" in sig.lower()
                badge = "#16a34a" if greenfield else "#d97706"
                label = "🌱 Greenfield" if greenfield else "⚠️ Has commerce/ordering tech"
                st.markdown(
                    f'**Tech signal (BuiltWith):** '
                    f'<span class="heat" style="background:{badge}">{label}</span>',
                    unsafe_allow_html=True)
                st.caption(sig)
        # Account context (from the company-discovery stage)
        acct = []
        if r.get("num_outlets_sg"): acct.append(f"**Outlets (SG):** {r['num_outlets_sg']}")
        if str(r.get("fit_score","")): acct.append(f"**Account fit:** {r['fit_score']}/5")
        if r.get("segment"): acct.append(f"**Segment:** {r['segment']}")
        if acct:
            st.markdown("  ·  ".join(acct))
        if r.get("suggested_products"):
            st.markdown(f"**Suggested products:** {r['suggested_products']}")
        if r.get("warm_intro") == "Y" or r.get("grant_eligible") == "Y":
            flags = []
            if r.get("warm_intro") == "Y": flags.append("🤝 Warm intro")
            if r.get("grant_eligible") == "Y": flags.append("💰 Grant eligible")
            st.markdown(" · ".join(flags))
        if r.get("fit_reason"):
            st.caption(f"Account fit reason: {r['fit_reason']}")
        st.markdown(f"**Company topline:** {r.get('company_topline','')}")
        st.markdown(f"**How to target:** {r.get('how_to_target','')}")
        st.markdown(f"**Position & bio:** {r.get('position_bio','')}")
        st.markdown(f"**Past experience:** {r.get('past_experience','')}")
        st.markdown(f"**CVP vs lead:** {r.get('cvp','')}")
        st.markdown(f"**Latest company press:** {r.get('company_press','')}")
        st.success(f"**Personalised sales paragraph:**\n\n{r.get('sales_paragraph','')}")
