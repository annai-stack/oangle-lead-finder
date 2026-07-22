"""
Oangle Lead Finder — Interactive UI
Run with: streamlit run app.py
"""

import os
import io
import html
import json
from pathlib import Path

import anthropic
import pandas as pd
import streamlit as st

import lead_finder as lf
import apollo_client
import client_config
import engine

# ---------------------------------------------------------------------------
# Page config + styling
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Akin Lead Finder",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Heat / priority band → colour, used by the engine (generic) renderers. Covers
# both the 1A→4 and Very High→Low band sets; falls back to grey for anything else.
BAND_COLORS = {
    "1A": "#dc2626", "1B": "#ea580c", "2": "#d97706", "3": "#2563eb", "4": "#6b7280",
    "Very High": "#dc2626", "High": "#ea580c", "Medium": "#2563eb", "Low": "#6b7280",
}


def _band_color(band) -> str:
    return BAND_COLORS.get(str(band).strip(), "#6b7280")

st.markdown("""
<style>
    .score-5 { background:#16a34a; color:white; padding:2px 8px; border-radius:4px; font-weight:700; }
    .score-4 { background:#2563eb; color:white; padding:2px 8px; border-radius:4px; font-weight:700; }
    .score-3 { background:#d97706; color:white; padding:2px 8px; border-radius:4px; font-weight:700; }
    .score-2 { background:#dc2626; color:white; padding:2px 8px; border-radius:4px; font-weight:700; }
    .score-1 { background:#6b7280; color:white; padding:2px 6px; border-radius:4px; font-weight:700; }
    .warm-badge  { background:#7c3aed; color:white; padding:2px 6px; border-radius:4px; font-size:0.75em; }
    .grant-badge { background:#0891b2; color:white; padding:2px 6px; border-radius:4px; font-size:0.75em; }
    div[data-testid="stMetricValue"] { font-size:2rem; }
    .nav-breadcrumb { color:#94a3b8; font-size:0.82rem; margin-bottom:0.25rem; }
</style>
""", unsafe_allow_html=True)

LEAD_FINDER_DIR = Path(__file__).parent
ENGINE_RUNS_DIR = LEAD_FINDER_DIR / "engine_runs"   # persisted generic-engine runs (per workspace)

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

_defaults: dict = {
    "page":            "landing",
    "leads":           [],
    "contacts":        pd.DataFrame(),
    "contacts_debug":  pd.DataFrame(),
    # Generic-engine state (client selection + engine.py runs)
    "client_key":      None,          # selected CLIENTS key, or "__custom__"
    "active_profile":  None,          # resolved client_config profile dict
    "custom_profile":  None,          # generic-engine profile built from the form
    "engine_leads":    [],            # engine.find_accounts() output
    "engine_contacts": [],            # engine.find_leads() output
    "engine_cost":     None,          # last run cost breakdown
}
for _k, _v in _defaults.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ---------------------------------------------------------------------------
# Sidebar — API keys (always visible) + page-specific config
# ---------------------------------------------------------------------------

with st.sidebar:
    st.image(
        "https://placehold.co/180x48/1e293b/ffffff?text=Akin+Lead+Finder",
        use_container_width=True,
    )
    if st.session_state.get("active_profile"):
        st.caption(f"Workspace: **{st.session_state.active_profile['client_name']}**")
    st.markdown("---")

    # Anthropic
    _anth_secret = (
        st.secrets.get("ANTHROPIC_API_KEY", "")
        if hasattr(st, "secrets")
        else os.environ.get("ANTHROPIC_API_KEY", "")
    )
    # A configured key stays silent — the status badges were sidebar clutter in
    # demos. The input only appears when a key is genuinely missing.
    if _anth_secret:
        anthropic_key = _anth_secret
    else:
        anthropic_key = st.text_input("🔑 Anthropic API Key", type="password", placeholder="sk-ant-...")

    # Apollo
    _apollo_secret = (
        st.secrets.get("APOLLO_API_KEY", "")
        if hasattr(st, "secrets")
        else os.environ.get("APOLLO_API_KEY", "")
    )
    if _apollo_secret:
        apollo_key = _apollo_secret
    else:
        apollo_key = st.text_input("🔗 Apollo API Key", type="password", placeholder="apollo-key...")

    # Hunter.io
    _hunter_secret = (
        st.secrets.get("HUNTER_API_KEY", "")
        if hasattr(st, "secrets")
        else os.environ.get("HUNTER_API_KEY", "")
    )
    if _hunter_secret:
        hunter_key = _hunter_secret
    else:
        hunter_key = st.text_input("🔍 Hunter.io API Key", type="password", placeholder="hunter-key...")

    # BuiltWith (optional, no UI input — secret/env only; enriches tech signals)
    builtwith_key = (
        st.secrets.get("BUILTWITH_API_KEY", "")
        if hasattr(st, "secrets")
        else os.environ.get("BUILTWITH_API_KEY", "")
    )

    _page = st.session_state.page

    # Account Leads run config
    if _page == "new_run_account":
        st.markdown("---")
        st.subheader("⚙️ Run Config")
        selected_segments = st.multiselect(
            "Segments to search", options=lf.TARGET_SEGMENTS, default=lf.TARGET_SEGMENTS,
        )
        max_leads      = st.slider("Max leads per segment", 1, 15, lf.MAX_LEADS_PER_SEGMENT)
        min_score      = st.slider("Minimum fit score", 1, 5, lf.MIN_FIT_SCORE)
        require_source = st.checkbox("Require source URL", value=lf.REQUIRE_SOURCE_URL)
        st.markdown("---")
        st.subheader("🤝 Warm Intro Brands")
        warm_brands_input = st.text_area(
            "One brand per line",
            value="\n".join(sorted(lf.WARM_INTRO_BRANDS)),
            height=90,
        )
        run_account_btn = st.button("🚀  Run Account Lead Finder", type="primary", use_container_width=True)
    else:
        selected_segments = lf.TARGET_SEGMENTS
        max_leads = lf.MAX_LEADS_PER_SEGMENT
        min_score = lf.MIN_FIT_SCORE
        require_source = lf.REQUIRE_SOURCE_URL
        warm_brands_input = "\n".join(sorted(lf.WARM_INTRO_BRANDS))
        run_account_btn = False

    # Contact Leads config
    if _page == "new_run_contact":
        st.markdown("---")
        st.subheader("⚙️ Contact Config")
        # Vendor names stay out of the UI, but the option VALUES are what the
        # run logic compares against — so only the labels are re-worded.
        contact_source = st.radio(
            "Data source",
            ["Apollo.io", "Hunter.io", "Both"],
            format_func=lambda v: {
                "Apollo.io": "Directory (names, emails, phones)",
                "Hunter.io": "Email finder",
                "Both": "Both sources",
            }[v],
            horizontal=True,
            help="Directory: names, emails and phones. Email finder: email-focused. "
                 "Both: merged results.",
        )
        max_contacts   = st.slider("Max contacts per company", 1, 10, 3)
        contact_titles = st.multiselect(
            "Title filter",
            options=apollo_client.DEFAULT_TITLES,
            default=apollo_client.DEFAULT_TITLES,
            help="Applies to directory searches.",
        )
    else:
        contact_source = "Both"
        max_contacts   = 3
        contact_titles = apollo_client.DEFAULT_TITLES

# ---------------------------------------------------------------------------
# Keys dict for the generic engine (engine.py expects this shape)
# ---------------------------------------------------------------------------

KEYS = {
    "ANTHROPIC_API_KEY": anthropic_key,
    "APOLLO_API_KEY":    apollo_key,
    "HUNTER_API_KEY":    hunter_key,
    "BUILTWITH_API_KEY": builtwith_key,
}

# ---------------------------------------------------------------------------
# Navigation helpers
# ---------------------------------------------------------------------------


def _nav(page: str) -> None:
    st.session_state.page = page
    st.rerun()


def _back(label: str = "← Back", to: str = "landing") -> None:
    if st.button(label, key=f"back_{to}"):
        _nav(to)


def _breadcrumb(*parts: str) -> None:
    st.markdown(
        '<p class="nav-breadcrumb">' + " &rsaquo; ".join(parts) + "</p>",
        unsafe_allow_html=True,
    )

# ---------------------------------------------------------------------------
# Shared rendering helpers
# ---------------------------------------------------------------------------


def _leads_to_df(leads: list[dict]) -> pd.DataFrame:
    rows = []
    for l in leads:
        products = l.get("suggested_products", [])
        rows.append({
            "Brand":    l.get("brand_name", ""),
            "Company":  l.get("company_name", ""),
            "Outlets":  l.get("num_outlets_sg", "?"),
            "Score":    l.get("fit_score", ""),
            "Why":      l.get("fit_reason", ""),
            "Products": ", ".join(products) if isinstance(products, list) else products,
            "Conf":     l.get("confidence", ""),
            "Grant":    l.get("grant_eligible", ""),
            "Warm":     l.get("warm_intro", ""),
            "Website":  l.get("website", ""),
            "Source":   l.get("source_url", ""),
            "Segment":  l.get("segment", ""),
        })
    return pd.DataFrame(rows)


def _csv_to_display_df(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path)
    raw["fit_score"] = pd.to_numeric(raw["fit_score"], errors="coerce")
    return pd.DataFrame({
        "Brand":    raw.get("brand_name", ""),
        "Company":  raw.get("company_name", ""),
        "Outlets":  raw.get("num_outlets_sg", "?"),
        "Score":    raw["fit_score"],
        "Why":      raw.get("fit_reason", ""),
        "Products": raw.get("suggested_products", ""),
        "Conf":     raw.get("confidence", ""),
        "Grant":    raw.get("grant_eligible", ""),
        "Warm":     raw.get("warm_intro", ""),
        "Website":  raw.get("website", ""),
        "Source":   raw.get("source_url", ""),
        "Segment":  raw.get("segment", ""),
    })


def _color_score(val):
    colors = {5: "#dcfce7", 4: "#dbeafe", 3: "#fef3c7", 2: "#fee2e2", 1: "#f3f4f6"}
    return f"background-color: {colors.get(val, 'white')}"


def _render_account_leads(df: pd.DataFrame, key: str, dl_label: str = "⬇️  Download CSV") -> None:
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total leads", len(df))
    m2.metric("Score 5 (Ideal)", (df["Score"] == 5).sum())
    m3.metric("Score 4 (Strong)", (df["Score"] == 4).sum())
    # A run that never collected these (an older CSV, or a client with no warm /
    # grant angle) must read "—", not "0" — zero claims we looked and found none.
    def _flag_metric(col: str) -> str | int:
        if col not in df.columns:
            return "—"
        vals = df[col].astype(str).str.strip()
        return "—" if (vals == "").all() else int((vals.str.upper() == "Y").sum())
    m4.metric("Warm intros", _flag_metric("Warm"))
    m5.metric("Grant eligible", _flag_metric("Grant"))
    st.markdown("---")

    fc1, fc2, fc3 = st.columns(3)
    with fc1:
        score_f = st.multiselect("Filter by score", [5, 4, 3, 2, 1], default=[5, 4, 3, 2, 1], key=f"sf_{key}")
    with fc2:
        seg_f = st.multiselect(
            "Filter by segment",
            options=df["Segment"].dropna().unique().tolist(),
            default=df["Segment"].dropna().unique().tolist(),
            key=f"segf_{key}",
        )
    with fc3:
        warm_only  = st.checkbox("Warm only",        key=f"w_{key}")
        grant_only = st.checkbox("Grant eligible",   key=f"g_{key}")

    filtered = df[df["Score"].isin(score_f) & df["Segment"].isin(seg_f)]
    if warm_only:  filtered = filtered[filtered["Warm"] == "Y"]
    if grant_only: filtered = filtered[filtered["Grant"] == "Y"]

    st.markdown(f"**{len(filtered)} leads shown**")
    styled = filtered.style.map(_color_score, subset=["Score"])
    st.dataframe(styled, use_container_width=True, hide_index=True, height=450)

    st.markdown("---")
    buf = io.StringIO()
    filtered.to_csv(buf, index=False)
    st.download_button(
        dl_label, data=buf.getvalue(),
        file_name=f"oangle_account_leads_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv",
        mime="text/csv", use_container_width=True,
    )

    st.markdown("---")
    st.subheader("📋 Lead Cards")
    for _, row in filtered.iterrows():
        score  = row["Score"]
        w_html = '<span class="warm-badge">🤝 Warm intro</span>'   if row["Warm"]  == "Y" else ""
        g_html = '<span class="grant-badge">💰 Grant eligible</span>' if row["Grant"] == "Y" else ""
        with st.expander(f"{row['Brand']}  —  {row['Outlets']} outlets  ·  Score {score}/5"):
            st.markdown(
                f'<span class="score-{score}">Score {score}/5</span> {w_html} {g_html}',
                unsafe_allow_html=True,
            )
            c1, c2 = st.columns(2)
            with c1:
                st.markdown(f"**Company:** {row['Company']}")
                st.markdown(f"**Segment:** {row['Segment']}")
                st.markdown(f"**Outlets:** {row['Outlets']}")
                st.markdown(f"**Confidence:** {row['Conf']}")
            with c2:
                st.markdown(f"**Products:** {row['Products']}")
                if row.get("Website"): st.markdown(f"**Website:** [{row['Website']}]({row['Website']})")
                if row.get("Source"):  st.markdown(f"**Source:** [{row['Source']}]({row['Source']})")
            st.markdown(f"**Fit reason:** {row['Why']}")


def _render_contact_results(contacts_df: pd.DataFrame) -> None:
    if contacts_df.empty:
        st.info("No contacts to display.")
        return

    # Debug expander — shows per-company, per-source counts and any errors
    debug_df: pd.DataFrame = st.session_state.get("contacts_debug", pd.DataFrame())
    if not debug_df.empty:
        with st.expander("🔍 API response log — click to diagnose missing data", expanded=False):
            st.dataframe(debug_df, use_container_width=True, hide_index=True)
            errors = debug_df[debug_df["Error"] != ""]
            if not errors.empty:
                st.warning(f"{len(errors)} error(s) — see Error column above for details.")
            zeros = debug_df[(debug_df["Error"] == "") & (debug_df["Returned"] == 0)]
            if not zeros.empty:
                st.info(
                    f"{len(zeros)} company/source pair(s) returned 0 contacts — "
                    "the domain may not be indexed by that provider."
                )

    total    = (~contacts_df["Contact Name"].str.startswith("[")).sum()
    has_email = (contacts_df["Email"].str.len() > 0).sum()
    has_phone = (contacts_df["Phone"].str.len() > 0).sum()
    companies = contacts_df["Brand"].nunique()

    cm1, cm2, cm3, cm4 = st.columns(4)
    cm1.metric("Companies covered", int(companies))
    cm2.metric("Contacts found",    int(total))
    cm3.metric("With email",        int(has_email))
    cm4.metric("With phone",        int(has_phone))
    st.markdown("---")

    brand_opts     = contacts_df["Brand"].unique().tolist()
    company_filter = st.multiselect("Filter by company", options=brand_opts, default=brand_opts, key="cf_contact")
    display_df     = contacts_df[contacts_df["Brand"].isin(company_filter)]
    st.markdown(f"**{len(display_df)} rows shown**")

    cols_order = [
        "Brand", "Company", "Score", "Outlets", "Segment",
        "Contact Name", "Contact Title", "Email", "Phone", "LinkedIn",
        "Suggested Products", "Grant Eligible", "Warm Intro", "Website",
    ]
    show_cols = [c for c in cols_order if c in display_df.columns]
    st.dataframe(display_df[show_cols], use_container_width=True, hide_index=True, height=450)
    st.markdown("---")

    dl1, dl2 = st.columns(2)
    with dl1:
        buf_all = io.StringIO()
        contacts_df.to_csv(buf_all, index=False)
        st.download_button(
            "⬇️  Download ALL contacts", data=buf_all.getvalue(),
            file_name=f"oangle_contacts_all_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv", use_container_width=True,
        )
    with dl2:
        buf_sel = io.StringIO()
        display_df.to_csv(buf_sel, index=False)
        st.download_button(
            f"⬇️  Download selected ({len(company_filter)} co.)", data=buf_sel.getvalue(),
            file_name=f"oangle_contacts_selected_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv", use_container_width=True,
        )

    st.markdown("---")
    st.subheader("👤 Contact Cards")
    for brand, group in display_df.groupby("Brand"):
        row0  = group.iloc[0]
        score = row0.get("Score", "")
        with st.expander(f"{brand}  —  Score {score}/5  ·  {len(group)} contact(s)"):
            for _, cr in group.iterrows():
                st.markdown(f"**{cr['Contact Name']}** — {cr.get('Contact Title', '')}")
                cols = st.columns(3)
                if cr.get("Email"):    cols[0].markdown(f"📧 {cr['Email']}")
                if cr.get("Phone"):    cols[1].markdown(f"📞 {cr['Phone']}")
                if cr.get("LinkedIn"): cols[2].markdown(f"[LinkedIn]({cr['LinkedIn']})")
                st.markdown("---")


# ---------------------------------------------------------------------------
# Page renderers
# ---------------------------------------------------------------------------

def render_landing() -> None:
    st.title("🎯 Akin Lead Finder")
    st.caption("AI-powered B2B prospect discovery")
    st.markdown("---")

    col1, col2 = st.columns(2, gap="large")

    # ── Card 1: Select a Client ────────────────────────────────────────────
    with col1:
        st.markdown("### 📇 Select a Client")
        st.markdown(
            "Run against a **pre-configured client profile** — ICP, value "
            "proposition and scoring rubric already tuned."
        )
        clients = client_config.list_clients()
        labels  = {c["name"]: c["key"] for c in clients}
        picked  = st.selectbox("Client workspace", options=list(labels.keys()), key="client_pick")
        st.markdown(" ")
        if st.button("Continue →", type="primary", use_container_width=True, key="client_go"):
            key = labels[picked]
            st.session_state.client_key     = key
            st.session_state.active_profile = client_config.get_client(key)
            st.session_state.engine_leads    = []
            st.session_state.engine_contacts = []
            # Oangle keeps its original, more richly-tailored flow; every other
            # client runs through the generic engine.
            _nav("new_run_type" if key == "oangle" else "engine_run_type")

    # ── Card 2: Generic Engine ─────────────────────────────────────────────
    with col2:
        st.markdown("### ⚙️ Generic Engine")
        st.markdown(
            "**Define your own** target profile on the fly — client name, ICP, "
            "value proposition and decision-maker titles — then run the same "
            "engine against any market or industry."
        )
        st.markdown(" ")
        if st.button("Define a profile →", use_container_width=True, key="generic_go"):
            st.session_state.client_key     = "__custom__"
            st.session_state.engine_leads    = []
            st.session_state.engine_contacts = []
            _nav("generic_profile")

    st.markdown("---")
    csv_count = len(list(LEAD_FINDER_DIR.glob("leads_fnb_*.csv")))
    if st.button(f"📁 View previous Oangle runs ({csv_count} saved) →",
                 disabled=csv_count == 0):
        _nav("view_previous")


def render_new_run_type() -> None:
    _back(to="landing")
    _breadcrumb("Home", "New Run")
    st.title("New Run")
    st.markdown("### What type of leads do you want to build?")
    st.markdown("---")

    col1, col2 = st.columns(2, gap="large")
    with col1:
        st.markdown("#### 📋 Account Leads")
        st.markdown("""
Search the web for F&B chains in Singapore, scored and ranked against Oangle's ICP.

**Output:**
- Brand & company name, outlet count
- Fit score 1–5 with reasoning
- Suggested products (POS / SOK / ORB / DMB)
- Grant eligibility & warm intro flags
        """)
        if st.button("Account Leads →", type="primary", use_container_width=True):
            st.session_state.leads = []
            _nav("new_run_account")

    with col2:
        st.markdown("#### 👤 Contact Leads")
        st.markdown("""
Enrich an existing account leads list with decision-maker contact data.

**Adds to each company:**
- Contact name & job title
- Work email & phone number
- LinkedIn profile URL
        """)
        if st.button("Contact Leads →", use_container_width=True):
            st.session_state.contacts = pd.DataFrame()
            _nav("new_run_contact")


def render_new_run_account() -> None:
    _back(to="new_run_type")
    _breadcrumb("Home", "New Run", "Account Leads")
    st.title("Account Lead Finder")
    st.caption("Configure your run in the sidebar, then click **Run Account Lead Finder**.")
    st.markdown("---")

    # --- Run logic ---
    if run_account_btn:
        if not anthropic_key:
            st.error("Anthropic API key required — add it in the sidebar.")
            st.stop()
        if not selected_segments:
            st.error("Select at least one segment.")
            st.stop()

        lf.MAX_LEADS_PER_SEGMENT = max_leads
        lf.MIN_FIT_SCORE         = min_score
        lf.REQUIRE_SOURCE_URL    = require_source
        lf.WARM_INTRO_BRANDS     = {b.strip().lower() for b in warm_brands_input.splitlines() if b.strip()}

        client  = anthropic.Anthropic(api_key=anthropic_key)
        all_raw: list[dict] = []
        st.session_state.leads = []

        progress_bar = st.progress(0, text="Starting…")
        placeholder  = st.empty()

        for i, segment in enumerate(selected_segments):
            progress_bar.progress(
                i / len(selected_segments),
                text=f"Searching: **{segment}** ({i+1}/{len(selected_segments)})",
            )
            try:
                with st.spinner(f"Searching {segment}…"):
                    all_raw.extend(lf.search_segment(client, segment))
            except RuntimeError as exc:
                progress_bar.empty()
                st.error(f"**API error while searching '{segment}':**\n\n{exc}", icon="🚨")
                if "403" in str(exc):
                    st.info(
                        "A 403 error means your Anthropic account does not have access to the "
                        "web search tool. Enable it at **console.anthropic.com → Settings → "
                        "Features** or contact Anthropic support.",
                        icon="ℹ️",
                    )
                elif "401" in str(exc):
                    st.info("A 401 error means the Anthropic API key is invalid or expired. Check the key in Streamlit secrets.", icon="ℹ️")
                st.stop()
            processed = lf.post_process(list(all_raw))
            st.session_state.leads = processed
            if processed:
                placeholder.dataframe(_leads_to_df(processed), use_container_width=True, hide_index=True)

        progress_bar.progress(1.0, text=f"✅ Done — {len(st.session_state.leads)} unique leads found")

    # --- Results ---
    if st.session_state.leads:
        st.markdown("---")
        _render_account_leads(_leads_to_df(st.session_state.leads), key="acct_run")
    elif not run_account_btn:
        c1, c2 = st.columns(2)
        with c1:
            st.info(
                "Select your run settings in the sidebar and click **Run Account Lead Finder** to start.",
                icon="👈",
            )


def render_new_run_contact() -> None:
    _back(to="new_run_type")
    _breadcrumb("Home", "New Run", "Contact Leads")
    st.title("Contact Lead Finder")
    st.caption("Enrich companies with decision-maker contact data.")
    st.markdown("---")

    if not apollo_key and not hunter_key:
        st.warning("A contact-data key is required — add one in the sidebar.", icon="🔑")
        return

    # ── Step 1: Source ────────────────────────────────────────────────────────
    st.markdown("### Step 1 — Choose your company source")

    source = st.radio(
        "source",
        ["Existing account leads", "Enter company/brand names manually"],
        label_visibility="collapsed",
    )
    st.markdown("---")

    account_df_source: pd.DataFrame | None = None
    selected_brands: list[str] = []

    # ── Source A: previous CSV ─────────────────────────────────────────────
    if source == "Existing account leads":
        csv_files = sorted(
            LEAD_FINDER_DIR.glob("leads_fnb_*.csv"),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        if not csv_files:
            st.warning("No previous account lead runs found. Run **Account Leads** first.", icon="📂")
            return

        st.markdown("### Step 2 — Select account leads run")
        selected_csv = st.selectbox(
            "Account leads run",
            options=[p.name for p in csv_files],
            label_visibility="collapsed",
        )
        account_df_source = _csv_to_display_df(LEAD_FINDER_DIR / selected_csv)

        # Preview the loaded list
        with st.expander(f"Preview — {len(account_df_source)} companies in this run", expanded=False):
            st.dataframe(
                account_df_source[["Brand", "Score", "Outlets", "Segment", "Website"]],
                use_container_width=True, hide_index=True,
            )

        st.markdown("### Step 3 — Select companies to enrich")
        brand_options = account_df_source["Brand"].dropna().unique().tolist()

        select_mode = st.radio(
            "Company selection",
            ["All companies", "Select specific companies"],
            horizontal=True,
        )
        if select_mode == "All companies":
            selected_brands = brand_options
            st.caption(f"All {len(brand_options)} companies selected")
        else:
            selected_brands = st.multiselect(
                "Choose companies",
                options=brand_options,
                placeholder="Search and select companies…",
            )
            if not selected_brands:
                st.info("Select at least one company to continue.")

    # ── Source B: manual names ─────────────────────────────────────────────
    else:
        st.markdown("### Step 2 — Enter company/brand names")
        st.caption("Companies are matched by name keyword. One name per line.")
        manual_text = st.text_area(
            "Company names",
            placeholder="McDonald's Singapore\nKFC Singapore\nToastBox",
            height=160,
            label_visibility="collapsed",
        )
        manual_names = [n.strip() for n in manual_text.splitlines() if n.strip()]

        if manual_names:
            st.caption(f"{len(manual_names)} companies entered")
            account_df_source = pd.DataFrame({
                "Brand":    manual_names,
                "Company":  manual_names,
                "Outlets":  ["?"] * len(manual_names),
                "Score":    [None] * len(manual_names),
                "Segment":  ["manual"] * len(manual_names),
                "Website":  [""] * len(manual_names),
                "Products": [""] * len(manual_names),
                "Grant":    [""] * len(manual_names),
                "Warm":     [""] * len(manual_names),
            })
            selected_brands = manual_names

    # ── Fetch button ───────────────────────────────────────────────────────
    st.markdown("---")
    use_keyword = source != "Existing account leads"

    if selected_brands and account_df_source is not None:
        if st.button(
            f"🔍  Fetch contacts for {len(selected_brands)} {'company' if len(selected_brands) == 1 else 'companies'}",
            type="primary",
        ):
            progress_bar = st.progress(0, text="Starting contact enrichment…")

            def _progress(i: int, total: int, brand: str) -> None:
                progress_bar.progress(i / total, text=f"Fetching: **{brand}** ({i+1}/{total})")

            contacts_df, debug_df = apollo_client.enrich_leads(
                leads_df=account_df_source,
                brand_filter=selected_brands,
                titles=contact_titles or None,
                max_per_company=max_contacts,
                progress_callback=_progress,
                use_keyword=use_keyword,
                apollo_key=apollo_key if contact_source in ("Apollo.io", "Both") else None,
                hunter_key=hunter_key if contact_source in ("Hunter.io", "Both") else None,
            )
            st.session_state.contacts       = contacts_df
            st.session_state.contacts_debug = debug_df
            progress_bar.progress(1.0, text="✅ Done")

    # ── Results ───────────────────────────────────────────────────────────
    if not st.session_state.contacts.empty:
        st.markdown("---")
        st.markdown("### Results")
        _render_contact_results(st.session_state.contacts)


def render_view_previous() -> None:
    _back(to="landing")
    _breadcrumb("Home", "Previous Runs")
    st.title("Previous Runs")
    st.markdown("---")

    csv_files = sorted(
        LEAD_FINDER_DIR.glob("leads_fnb_*.csv"),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    if not csv_files:
        st.info("No previous runs found.", icon="📂")
        return

    selected_label = st.selectbox(
        f"{len(csv_files)} run(s) available",
        options=[p.name for p in csv_files],
        label_visibility="collapsed",
    )
    try:
        prev_df = _csv_to_display_df(LEAD_FINDER_DIR / selected_label)
        st.caption(f"Loaded: `{selected_label}` · {len(prev_df)} leads")
        st.markdown("---")
        _render_account_leads(
            prev_df,
            key=f"prev_{selected_label}",
            dl_label=f"⬇️  Download {selected_label}",
        )
    except Exception as exc:
        st.error(f"Could not load file: {exc}")


# ===========================================================================
# Generic engine — client picker profile + engine.py-powered run pages
# ===========================================================================

_DEFAULT_SCORING = (
    "Sample Scoring Logic\n\n"
    "1. ICP / Product Fit (40%) — how well the company matches the ideal "
    "customer profile above.\n"
    "2. Role & Influence (40%) — the contact's decision-making authority and "
    "budget ownership.\n"
    "3. Reachability & Intent (20%) — warm channels, recent activity, expansion "
    "or buying signals.\n\n"
    "Heat Rank tiers:\n"
    "1A – Immediate: ideal ICP + senior decision-maker\n"
    "1B – Strong: good fit + influential role\n"
    "2  – Moderate: partial fit or mid-level contact\n"
    "3  – Contextual: borderline fit, nurture\n"
    "4  – Low weight: weak fit, deprioritise"
)


def render_generic_profile() -> None:
    _back(to="landing")
    _breadcrumb("Home", "Generic Engine")
    st.title("⚙️ Define a Target Profile")
    st.caption("These fields drive the discovery, scoring and outreach prompts. "
               "Nothing is saved — it's used for this run only.")
    st.markdown("---")

    with st.form("generic_profile_form"):
        client_name = st.text_input(
            "Client / product name", placeholder="e.g. Acme Cloud")
        one_liner = st.text_area(
            "One-line description of the client",
            placeholder="Acme Cloud provides managed Kubernetes hosting for APAC SMEs.",
            height=70)
        icp = st.text_area(
            "Ideal Customer Profile (who you sell to)",
            placeholder="Mid-market SaaS companies (50–500 staff) running their own "
                        "infra and feeling scaling pain. Avoid pre-revenue startups.",
            height=90)
        value_prop = st.text_area(
            "Value proposition (why they should care)",
            placeholder="Cuts DevOps overhead and cloud spend by ~30% with a "
                        "fully-managed, autoscaling platform.",
            height=90)

        titles = st.multiselect(
            "Decision-maker titles to target",
            options=apollo_client.DEFAULT_TITLES,
            default=apollo_client.DEFAULT_TITLES[:6],
            accept_new_options=True,
            help="Seniority/roles pulled for each company. "
                 "Type any title and press Enter to add one that isn't listed.")

        with st.expander("Advanced — scoring rubric & heat-rank labels"):
            scoring_logic = st.text_area(
                "Scoring logic (shown to the model)", value=_DEFAULT_SCORING, height=180)
            heat_ranks_raw = st.text_input(
                "Heat-rank labels (comma-separated, hottest → coldest)",
                value="1A, 1B, 2, 3, 4")

        submitted = st.form_submit_button("Build profile & continue →", type="primary")

    if submitted:
        errors = []
        if not client_name.strip():  errors.append("Client / product name")
        if not icp.strip():          errors.append("Ideal Customer Profile")
        if not value_prop.strip():   errors.append("Value proposition")
        if errors:
            st.error("Please fill in: " + ", ".join(errors))
            return

        all_titles = [t.strip() for t in titles if t.strip()]
        heat_ranks = [h.strip() for h in heat_ranks_raw.split(",") if h.strip()] or ["1A", "1B", "2", "3", "4"]

        # Emit ALL keys the engine reads with profile['...'] (hard access) so a
        # partially-filled form can never KeyError mid-run.
        profile = {
            "client_name":     client_name.strip(),
            "client_one_liner": one_liner.strip() or client_name.strip(),
            "motion":          "outbound",
            "icp":             icp.strip(),
            "value_prop":      value_prop.strip(),
            "target_titles":   all_titles or apollo_client.DEFAULT_TITLES,
            "scoring_logic":   scoring_logic.strip() or _DEFAULT_SCORING,
            "heat_ranks":      heat_ranks,
            "account_columns": [],
        }
        st.session_state.custom_profile = profile
        st.session_state.active_profile = profile
        st.session_state.client_key     = "__custom__"
        _nav("engine_run_type")


def render_engine_run_type() -> None:
    profile = st.session_state.active_profile
    if not profile:
        _nav("landing"); return
    _back(to="landing")
    _breadcrumb("Home", profile["client_name"], "New Run")
    st.title(f"New Run — {profile['client_name']}")
    st.markdown("### What type of leads do you want to build?")
    st.markdown("---")

    col1, col2 = st.columns(2, gap="large")
    with col1:
        st.markdown("#### 📋 Account Leads")
        st.markdown(
            "Discover companies matching the ICP via live web search, scored and "
            "ranked into priority bands.\n\n"
            "**Output:** company, sector & size, priority band, why-it-scores, "
            "likely use case, how-to-target, and named champions.")
        if st.button("Account Leads →", type="primary", use_container_width=True):
            st.session_state.engine_leads = []
            _nav("engine_run_account")
    with col2:
        st.markdown("#### 👤 Contact Leads")
        st.markdown(
            "Discover companies **and** their decision-makers, with personalised "
            "outreach per contact.\n\n"
            "**Output:** contact name, title & email, heat rank, CVP and a "
            "ready-to-send sales paragraph.")
        if st.button("Contact Leads →", use_container_width=True):
            st.session_state.engine_contacts = []
            _nav("engine_run_contact")

    # Previous runs for THIS workspace
    prev = _list_engine_runs(st.session_state.client_key)
    st.markdown("---")
    if prev:
        st.markdown(f"#### 📁 Previous {profile['client_name']} runs")
        st.caption(f"{len(prev)} saved run(s) in this workspace.")
        if st.button(f"View previous runs ({len(prev)}) →"):
            _nav("engine_previous")
    else:
        st.caption("No previous runs saved for this workspace yet — results are saved automatically once a run completes.")


def _parse_companies(raw: str) -> list[dict]:
    """Parse a textarea of companies — one per line, optional `, URL` suffix."""
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if "," in line:
            name, url = line.split(",", 1)
            out.append({"name": name.strip(), "url": url.strip()})
        else:
            out.append({"name": line, "url": ""})
    return out


def _engine_inputs(profile: dict, key: str) -> dict:
    """Discovery inputs. Either discover by industry, or enter a list of specific
    companies. Returns a params dict; check _engine_ready before running."""
    mode = st.radio(
        "How should we source companies?",
        ["Discover by industry", "Enter specific companies"],
        horizontal=True, key=f"mode_{key}")

    params: dict = {"industries": [], "market": "", "size_band": "", "companies": []}
    if mode == "Discover by industry":
        industries_raw = st.text_area(
            "Industries to search (one per line, up to 3 used)",
            placeholder="software and internet\nfinancial services\nprofessional services",
            height=90, key=f"ind_{key}")
        params["industries"] = [i.strip() for i in industries_raw.splitlines() if i.strip()]
        c1, c2 = st.columns(2)
        params["market"]    = c1.text_input("Market / location", value="Singapore", key=f"mkt_{key}")
        params["size_band"] = c2.text_input("Company size band (optional)",
                                            placeholder="100-499", key=f"sz_{key}")
    else:
        companies_raw = st.text_area(
            "Companies — one per line. Optionally add a URL after a comma.",
            placeholder="Grab\nSea Limited, https://sea.com\nPatSnap, https://patsnap.com",
            height=160, key=f"co_{key}")
        params["companies"] = _parse_companies(companies_raw)
        if params["companies"]:
            st.caption(f"{len(params['companies'])} "
                       f"compan{'y' if len(params['companies']) == 1 else 'ies'} entered")
        params["market"] = st.text_input("Market / location", value="Singapore", key=f"mkt1_{key}")
    return params


def _engine_ready(params: dict) -> bool:
    return bool(params.get("industries") or params.get("companies"))


def _merge_costs(costs: list[dict]) -> dict:
    """Sum a list of cost_model.cost() dicts into one aggregate breakdown."""
    keys = ["input_tokens", "output_tokens", "cache_read_input_tokens",
            "cache_creation_input_tokens", "web_search_requests",
            "token_cost", "search_cost", "total"]
    out = {k: 0 for k in keys}
    for c in costs or []:
        for k in keys:
            out[k] += c.get(k, 0) or 0
    return out


# ── Engine-run history (persist runs per workspace so they survive a refresh) ─

def _save_engine_run(client_key: str, profile: dict, mode: str,
                     records: list[dict], cost: dict | None) -> None:
    """Persist a completed engine run as JSON, keyed by workspace + mode + time.

    The full profile is stored so the viewer can re-render (band colours, name)
    without needing the original client_config entry — important for the
    on-the-fly Generic Engine profiles, which aren't saved anywhere else.
    """
    if not records:
        return
    try:
        ENGINE_RUNS_DIR.mkdir(exist_ok=True)
        ts   = pd.Timestamp.now().strftime("%Y%m%d_%H%M%S")
        safe = "".join(c if c.isalnum() else "_" for c in (client_key or "run"))
        payload = {
            "client_key":  client_key,
            "client_name": profile.get("client_name", ""),
            "mode":        mode,                 # "accounts" | "contacts"
            "timestamp":   ts,
            "cost":        cost,
            "profile":     profile,
            "records":     records,
        }
        (ENGINE_RUNS_DIR / f"{safe}_{mode}_{ts}.json").write_text(
            json.dumps(payload, default=str))
    except Exception:
        pass   # persistence is best-effort — never break a run over a failed write


def _list_engine_runs(client_key: str | None = None) -> list[dict]:
    """Return saved-run summaries (newest first), optionally filtered to one workspace."""
    if not ENGINE_RUNS_DIR.exists():
        return []
    out = []
    for p in sorted(ENGINE_RUNS_DIR.glob("*.json"),
                    key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            data = json.loads(p.read_text())
        except Exception:
            continue
        if client_key and data.get("client_key") != client_key:
            continue
        out.append({"path": p, "client_name": data.get("client_name", ""),
                    "mode": data.get("mode", ""), "timestamp": data.get("timestamp", ""),
                    "cost": data.get("cost"), "count": len(data.get("records", []))})
    return out


def _make_progress(total_hint: int = 1):
    """Return (on_event callback, cleanup fn) that drives a Streamlit progress bar."""
    bar    = st.progress(0.0, text="Starting…")
    status = st.empty()
    state  = {"total": max(total_hint, 1), "done": 0}

    def on_event(ev: dict) -> None:
        stage = ev.get("stage")
        if stage == "discover":
            status.info(f"🔍 {ev.get('msg', 'searching…')}")
        elif stage == "discovered":
            state["total"] = max(state["total"], int(ev.get("count", 1)))
            status.info(f"🔍 Discovered {ev.get('count', 0)} companies — enriching…")
        elif stage in ("enrich", "contacts"):
            msg = ev.get("msg") or f"{ev.get('company', '')} · {ev.get('count', '')} contacts"
            status.info(f"✨ {msg}")
        elif stage in ("account", "lead"):
            state["done"] += 1
            frac = min(state["done"] / max(state["total"], 1), 1.0)
            bar.progress(frac, text=f"Processed {state['done']} record(s)")

    def done(n: int) -> None:
        bar.progress(1.0, text=f"✅ Done — {n} record(s)")

    return on_event, done


def _cost_caption(cost: dict | None) -> None:
    if cost:
        st.caption(
            f"💸 Run cost ≈ **${cost.get('total', 0):.3f}** "
            f"({cost.get('input_tokens', 0):,} in / {cost.get('output_tokens', 0):,} out tokens, "
            f"{cost.get('web_search_requests', 0)} web searches)")


# ── Account-leads renderer (find_accounts output) ──────────────────────────

def _accounts_to_df(accounts: list[dict]) -> pd.DataFrame:
    rows = []
    for a in accounts:
        champs = "; ".join(
            f"{c.get('name','')}{' (' + c['title'] + ')' if c.get('title') else ''}".strip()
            for c in a.get("champions", []) if c.get("name"))
        rows.append({
            "Company":      a.get("company_name", ""),
            "Sector":       a.get("sector", ""),
            "Size":         a.get("company_size", ""),
            "Priority":     a.get("priority_band", ""),
            "Likely use case": a.get("likely_use_case", ""),
            "Champions":    champs,
            "Market":       a.get("market", ""),
            "Confidence":   a.get("confidence", ""),
            "Website":      a.get("website", ""),
            "Source":       a.get("source_url", ""),
        })
    return pd.DataFrame(rows)


def _render_engine_accounts(accounts: list[dict], profile: dict) -> None:
    df = _accounts_to_df(accounts)
    top_band = profile["heat_ranks"][0]
    m1, m2, m3 = st.columns(3)
    m1.metric("Companies", len(df))
    m2.metric(f"Top band ({top_band})", int((df["Priority"] == top_band).sum()))
    m3.metric("With source", int((df["Source"].str.len() > 0).sum()))
    st.markdown("---")

    bands = [b for b in profile["heat_ranks"] if b in df["Priority"].unique().tolist()]
    band_f = st.multiselect("Filter by priority band", options=bands, default=bands, key="eng_band_f")
    view = df[df["Priority"].isin(band_f)] if band_f else df
    st.markdown(f"**{len(view)} companies shown**")
    st.dataframe(view, use_container_width=True, hide_index=True, height=380)

    buf = io.StringIO(); view.to_csv(buf, index=False)
    st.download_button(
        "⬇️  Download CSV", data=buf.getvalue(),
        file_name=f"{profile['client_name'].lower().replace(' ', '_')}_accounts_"
                  f"{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv",
        mime="text/csv", use_container_width=True)

    st.markdown("---")
    st.subheader("📋 Account Cards")
    idx = {a.get("company_name", ""): a for a in accounts}
    for _, row in view.iterrows():
        a = idx.get(row["Company"], {})
        color = _band_color(row["Priority"])
        with st.expander(f"{row['Company']}  —  {row['Sector']}  ·  {row['Priority']}"):
            st.markdown(
                f'<span style="background:{color};color:white;padding:2px 8px;'
                f'border-radius:4px;font-weight:700">{row["Priority"]}</span>',
                unsafe_allow_html=True)
            c1, c2 = st.columns(2)
            with c1:
                st.markdown(f"**Sector:** {row['Sector']}")
                st.markdown(f"**Size:** {row['Size']}")
                st.markdown(f"**Market:** {row['Market']}  ·  **Confidence:** {row['Confidence']}")
                if row["Website"]: st.markdown(f"**Website:** [{row['Website']}]({row['Website']})")
                if row["Source"]:  st.markdown(f"**Source:** [{row['Source']}]({row['Source']})")
            with c2:
                if row["Champions"]: st.markdown(f"**Champions:** {row['Champions']}")
                if a.get("tech_signals"): st.markdown(f"**Tech signals:** {a['tech_signals']}")
            if a.get("why_it_scores"):  st.markdown(f"**Why it scores:** {a['why_it_scores']}")
            if row["Likely use case"]:  st.markdown(f"**Likely use case:** {row['Likely use case']}")
            if a.get("how_to_target"):  st.markdown(f"**How to target:** {a['how_to_target']}")
            if a.get("company_topline"): st.caption(a["company_topline"])
            if a.get("company_press"):   st.caption(f"Press: {a['company_press']}")


# ── AWS-deck-styled record cards (navy + AWS-orange, Tier 1/Tier 2 look) ───

# Palette + record/band classes lifted from the AKIN × AWS deck so the output
# reads like the deck's Tier 1 / Tier 2 sample records.
_DECK_CSS = """
<style>
.akin-rec{border:1px solid #1c3350;border-radius:14px;overflow:hidden;background:#102138;margin-bottom:16px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
.akin-rechead{background:#0c1f38;display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 16px;border-bottom:1px solid #1c3350}
.akin-rechead b{font-size:15px;color:#eef3fb;font-weight:700}
.akin-rechead .sub{color:#9fb2cc;font-size:12.5px;font-weight:400}
.akin-band{display:inline-flex;align-items:center;gap:7px;padding:5px 11px;border-radius:100px;font-size:12px;font-weight:700;white-space:nowrap}
.akin-band::before{content:"";width:8px;height:8px;border-radius:50%;background:currentColor}
.akin-band.vh{color:#37d99a;background:rgba(55,217,154,.14)}
.akin-band.hi{color:#ffb020;background:rgba(255,176,32,.14)}
.akin-band.md{color:#7f93ad;background:rgba(127,147,173,.14)}
.akin-band.lo{color:#ff6b6b;background:rgba(255,107,107,.13)}
.akin-row{display:grid;grid-template-columns:150px 1fr;gap:12px;padding:9px 16px;border-bottom:1px solid rgba(255,255,255,.05);font-size:13.3px;line-height:1.5}
.akin-row:last-child{border-bottom:none}
.akin-label{color:#607891;font-size:11px;letter-spacing:.04em}
.akin-val{color:#eef3fb}
.akin-row.t2{background:rgba(255,153,0,.09)}
.akin-row.t2 .akin-label{color:#ff9900}
.akin-plan{background:rgba(255,153,0,.07);border-top:1px solid #1c3350;padding:12px 16px}
.akin-plan h5{color:#ff9900;font-size:11px;letter-spacing:.08em;text-transform:uppercase;margin:0 0 6px}
.akin-plan p{color:#eef3fb;font-size:13.3px;line-height:1.6;margin:0}
.akin-plan .tone{color:#9fb2cc;font-size:11.5px;margin-top:8px;font-style:italic}
.akin-legend{display:flex;flex-wrap:wrap;gap:9px;margin:2px 0 12px}
.akin-tablewrap{overflow:auto;border:1px solid #1c3350;border-radius:12px}
.akin-table{width:100%;border-collapse:collapse;font-size:12px;min-width:900px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
.akin-table thead th{background:#0c1f38;color:#9fb2cc;text-align:left;padding:9px 12px;font-weight:600;font-size:11px;letter-spacing:.04em}
.akin-table tbody td{padding:9px 12px;border-top:1px solid rgba(255,255,255,.05);color:#9fb2cc;line-height:1.35;vertical-align:top}
.akin-table tbody td b{color:#eef3fb;font-weight:700}
.akin-table tbody tr:hover{background:rgba(255,255,255,.03)}
.akin-faint{color:#607891}
.akin-drill{display:grid;grid-template-columns:230px 1fr;gap:18px;margin-top:12px;border:1px solid #1c3350;border-radius:12px;padding:14px 16px;background:#102138;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif}
.akin-who b{font-size:16px;color:#eef3fb}
.akin-who .meta{color:#607891;font-size:11.5px;margin-top:8px;line-height:1.7}
.akin-who .meta span{color:#9fb2cc}
.akin-sig h5{color:#ff9900;font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;margin:0 0 4px}
.akin-sig p{color:#9fb2cc;font-size:13px;line-height:1.5;margin:0 0 11px}
</style>
"""


def _deck_band_class(rank: str, heat_ranks: list[str]) -> str:
    """Map a client's heat/priority label to a deck band colour (vh/hi/md/lo)."""
    try:
        i = heat_ranks.index(rank)
    except ValueError:
        return "md"
    if len(heat_ranks) <= 4:
        return ["vh", "hi", "md", "lo"][min(i, 3)]
    return ["vh", "hi", "md"][i] if i < 3 else "lo"   # 5-tier: 1A,1B,2 → vh,hi,md; rest → lo


def _deck_contact_card(r: dict, profile: dict) -> str:
    """Render one contact record as deck-style Tier 2 HTML (orange = Tier 2 fields)."""
    band  = _deck_band_class(str(r.get("heat_rank", "")), profile["heat_ranks"])
    name  = f"{r.get('first_name', '')} {r.get('last_name', '')}".strip() or "—"
    title = r.get("job_title", "")
    comp  = r.get("company_name", "")
    sub   = " · ".join(str(x) for x in [title, comp] if x)
    head  = (f'<div class="akin-rechead"><b>{html.escape(name)}'
             f'<span class="sub"> · {html.escape(sub)}</span></b>'
             f'<span class="akin-band {band}">{html.escape(str(r.get("heat_rank", "")))}</span></div>')

    rows: list[str] = []

    def row(label: str, val, t2: bool = False, raw: bool = False) -> None:
        if not val:
            return
        cell = val if raw else html.escape(str(val))
        rows.append(f'<div class="akin-row{" t2" if t2 else ""}">'
                    f'<div class="akin-label">{label}</div><div class="akin-val">{cell}</div></div>')

    sect_size = " · ".join(str(x) for x in [r.get("sector") or r.get("industry", ""),
                                            r.get("company_size", "")] if x)
    row("Sector · Size",   sect_size)
    row("Location",        r.get("location", ""))
    row("Org description", r.get("company_topline", ""))
    row("Why it scores",   r.get("why_it_scores", ""))
    row("Likely use case", r.get("likely_use_case", ""))
    row("Verified email",  r.get("email", ""), t2=True)
    li = r.get("linkedin_url", "")
    if li:
        row("LinkedIn", f'<a href="{html.escape(li)}" style="color:#ff9900" target="_blank">'
                        f'{html.escape(li)}</a>', t2=True, raw=True)
    row("Digital footprint", r.get("position_bio", ""),  t2=True)
    row("Why this person",   r.get("cvp", ""),            t2=True)
    row("How to engage",     r.get("how_to_target", ""),  t2=True)

    plan = r.get("sales_paragraph", "")
    plan_html = ""
    if plan:
        plan_html = ('<div class="akin-plan"><h5>Gen-AI outreach plan · illustrative</h5>'
                     f'<p>“{html.escape(plan)}”</p>'
                     '<p class="tone">Drafted from public profile &amp; role signals, matched to a '
                     'peer-to-peer tone. Reviewed &amp; edited before send — never auto-sent.</p></div>')
    return f'<div class="akin-rec">{head}{"".join(rows)}{plan_html}</div>'


def _deck_legend(profile: dict, present: list[str]) -> str:
    """Static band legend (deck-style chips) for the bands present in the run."""
    chips = "".join(
        f'<span class="akin-band {_deck_band_class(b, profile["heat_ranks"])}">{html.escape(b)}</span>'
        for b in profile["heat_ranks"] if b in present)
    return f'<div class="akin-legend">{chips}</div>'


def _deck_contacts_table(records: list[dict], profile: dict) -> str:
    """Worked-sample table (deck demo slide layout) of the filtered contacts."""
    head = ("<tr><th>Contact · Company</th><th>Sector · Size</th>"
            "<th>Why it scores — signals</th><th>Likely use case</th>"
            "<th>Champion + how to engage</th><th>Priority</th></tr>")
    body = []
    for r in records:
        band = _deck_band_class(str(r.get("heat_rank", "")), profile["heat_ranks"])
        name = f"{r.get('first_name', '')} {r.get('last_name', '')}".strip() or "—"
        comp = str(r.get("company_name", ""))
        sect = " · ".join(str(x) for x in [r.get("sector") or r.get("industry", ""),
                                           r.get("company_size", "")] if x)
        champ = ", ".join(str(x) for x in [r.get("job_title", ""), r.get("email", "")] if x)
        engage = r.get("how_to_target", "") or r.get("cvp", "")
        champ_cell = html.escape(champ)
        if engage:
            champ_cell += f'<br><span class="akin-faint">{html.escape(str(engage)[:170])}</span>'
        cells = [
            f"<b>{html.escape(name)}</b><br>{html.escape(comp)}",
            html.escape(sect),
            html.escape(str(r.get("why_it_scores", ""))),
            html.escape(str(r.get("likely_use_case", ""))),
            champ_cell,
            f'<span class="akin-band {band}">{html.escape(str(r.get("heat_rank", "")))}</span>',
        ]
        body.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
    return (f'<div class="akin-tablewrap"><table class="akin-table">'
            f'<thead>{head}</thead><tbody>{"".join(body)}</tbody></table></div>')


def _deck_drill(r: dict, profile: dict) -> str:
    """Drill-down for one contact — deck's who + signal + outreach layout."""
    band = _deck_band_class(str(r.get("heat_rank", "")), profile["heat_ranks"])
    name = f"{r.get('first_name', '')} {r.get('last_name', '')}".strip() or "—"
    li   = r.get("linkedin_url", "")
    meta = [f'<span>{html.escape(str(r.get("job_title", "") or "—"))}</span> · {html.escape(str(r.get("company_name", "")))}']
    meta.append(f'LinkedIn <span>{"✔ confirmed" if li else "—"}</span>')
    meta.append(f'Email <span>{html.escape(str(r.get("email", "") or "—"))}</span>')
    meta.append(f'Seniority <span>{html.escape(str(r.get("seniority", "") or "—"))}</span>')
    meta.append(f'Location <span>{html.escape(str(r.get("location", "") or "—"))}</span>')
    who = (f'<div class="akin-who"><span class="akin-band {band}">{html.escape(str(r.get("heat_rank", "")))}</span>'
           f'<div style="margin-top:10px"><b>{html.escape(name)}</b></div>'
           f'<div class="meta">{"<br>".join(meta)}</div></div>')

    sigs = [("Digital footprint / recent signal", r.get("position_bio", "")),
            ("Why the account scores",            r.get("why_it_scores", "")),
            ("Why this person",                   r.get("cvp", "")),
            ("How to engage",                     r.get("how_to_target", ""))]
    sig_html = "".join(f'<div class="akin-sig"><h5>{h}</h5><p>{html.escape(str(v))}</p></div>'
                       for h, v in sigs if v)
    plan = r.get("sales_paragraph", "")
    if plan:
        sig_html += ('<div class="akin-sig"><h5>Gen-AI outreach plan · illustrative</h5>'
                     f'<p style="color:#eef3fb">“{html.escape(plan)}”</p></div>')
    return f'<div class="akin-drill">{who}<div>{sig_html or "<p class=akin-faint>No enrichment detail.</p>"}</div></div>'


# ── Contact-leads renderer (find_leads / build_record output) ──────────────

def _render_engine_contacts(leads: list[dict], profile: dict) -> None:
    df = pd.DataFrame(leads)
    if df.empty or "heat_rank" not in df.columns:
        st.info("No contacts found for these companies.")
        return
    for col in ("email", "job_title", "company_name", "seniority", "linkedin_url"):
        if col not in df.columns: df[col] = ""
        df[col] = df[col].fillna("")

    top = profile["heat_ranks"][0]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Contacts", len(df))
    m2.metric(f"🔥 {top}", int((df["heat_rank"] == top).sum()))
    m3.metric("Companies", int(df["company_name"].nunique()))
    m4.metric("With email", int((df["email"].str.len() > 0).sum()))
    st.markdown("---")

    fc1, fc2 = st.columns([1, 2])
    with fc1:
        bands  = [b for b in profile["heat_ranks"] if b in df["heat_rank"].unique().tolist()]
        heat_f = st.multiselect("Heat rank", bands, default=bands, key="eng_heat_f")
    with fc2:
        co_opts = sorted(df["company_name"].unique().tolist())
        co_f    = st.multiselect("Company", co_opts, default=co_opts, key="eng_co_f")
    view = df[df["heat_rank"].isin(heat_f) & df["company_name"].isin(co_f)]
    records = view.to_dict("records")
    st.markdown(f"**{len(records)} leads — scored and contextualised**")

    # Worked-sample table (deck demo slide layout), band-coloured priority.
    st.markdown(_DECK_CSS, unsafe_allow_html=True)
    present = [b for b in profile["heat_ranks"] if b in view["heat_rank"].unique().tolist()]
    st.markdown(_deck_legend(profile, present), unsafe_allow_html=True)
    st.markdown(_deck_contacts_table(records, profile), unsafe_allow_html=True)

    buf = io.StringIO(); view.to_csv(buf, index=False)
    st.download_button(
        "⬇️  Download CSV", data=buf.getvalue(),
        file_name=f"{profile['client_name'].lower().replace(' ', '_')}_contacts_"
                  f"{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv",
        mime="text/csv", use_container_width=True)

    # Drill one lead down to a named champion (deck demo drill-down).
    if records:
        st.markdown("---")
        st.subheader("🔎 Drill into a lead")
        labels = [f"{(r.get('first_name','') + ' ' + r.get('last_name','')).strip() or '—'}"
                  f" · {r.get('company_name','')}  ({r.get('heat_rank','')})" for r in records]
        pick = st.selectbox("Champion", options=list(range(len(records))),
                            format_func=lambda i: labels[i], key="eng_drill")
        st.markdown(_deck_drill(records[pick], profile), unsafe_allow_html=True)


def render_engine_run_account() -> None:
    profile = st.session_state.active_profile
    if not profile:
        _nav("landing"); return
    _back(to="engine_run_type")
    _breadcrumb("Home", profile["client_name"], "Account Leads")
    st.title("Account Lead Finder")
    st.caption(f"Discovering companies that fit **{profile['client_name']}**'s ICP.")
    st.markdown("---")

    params = _engine_inputs(profile, key="acct")
    c1, c2 = st.columns(2)
    max_accounts  = c1.slider("Max companies", 1, 15, 5, key="eng_max_acct")
    use_builtwith = c2.checkbox("Use BuiltWith tech signals", value=bool(KEYS["BUILTWITH_API_KEY"]),
                                key="eng_bw_acct", disabled=not KEYS["BUILTWITH_API_KEY"])

    if st.button("🚀  Run Account Lead Finder", type="primary", disabled=not _engine_ready(params)):
        if not anthropic_key:
            st.error("Anthropic API key required — add it in the sidebar."); st.stop()
        client = anthropic.Anthropic(api_key=anthropic_key, timeout=600.0)
        total_hint = len(params["companies"]) or max_accounts
        on_event, done = _make_progress(total_hint)
        accounts, costs = [], []
        try:
            if params["companies"]:
                for co in params["companies"]:
                    res = engine.find_accounts(
                        client, KEYS, profile, company_name=co["name"], url=co["url"],
                        market=params["market"], use_builtwith=use_builtwith, on_event=on_event)
                    accounts += res["accounts"]; costs.append(res["cost"])
            else:
                res = engine.find_accounts(
                    client, KEYS, profile,
                    industries=params["industries"], market=params["market"],
                    size_band=params["size_band"], max_accounts=max_accounts,
                    use_builtwith=use_builtwith, on_event=on_event)
                accounts = res["accounts"]; costs.append(res["cost"])
        except Exception as exc:
            st.error(f"**Run failed:** {exc}", icon="🚨"); st.stop()
        st.session_state.engine_leads = accounts
        st.session_state.engine_cost  = _merge_costs(costs)
        _save_engine_run(st.session_state.client_key, profile, "accounts",
                         accounts, st.session_state.engine_cost)
        done(len(accounts))

    if st.session_state.engine_leads:
        st.markdown("---")
        _cost_caption(st.session_state.engine_cost)
        _render_engine_accounts(st.session_state.engine_leads, profile)
    elif not _engine_ready(params):
        st.info("Enter at least one industry, or a list of companies, to enable the run.", icon="👈")


def render_engine_run_contact() -> None:
    profile = st.session_state.active_profile
    if not profile:
        _nav("landing"); return
    _back(to="engine_run_type")
    _breadcrumb("Home", profile["client_name"], "Contact Leads")
    st.title("Contact Lead Finder")
    st.caption(f"Discovering decision-makers for **{profile['client_name']}** with personalised outreach.")
    st.markdown("---")

    if not (apollo_key or hunter_key):
        st.warning("Contact discovery needs a contact-data key — add one in the sidebar.", icon="🔑")
        return

    params = _engine_inputs(profile, key="cont")
    titles = st.multiselect("Decision-maker titles", options=profile["target_titles"],
                            default=profile["target_titles"], key="eng_titles",
                            accept_new_options=True,
                            help="Type any title and press Enter to add one that isn't listed.")
    c1, c2, c3 = st.columns(3)
    max_accounts   = c1.slider("Max companies", 1, 10, 3, key="eng_max_cont")
    contacts_per   = c2.slider("Contacts per company", 1, 5, 3, key="eng_cpc")
    use_builtwith  = c3.checkbox("BuiltWith signals", value=bool(KEYS["BUILTWITH_API_KEY"]),
                                 key="eng_bw_cont", disabled=not KEYS["BUILTWITH_API_KEY"])

    if st.button("🚀  Run Contact Lead Finder", type="primary", disabled=not _engine_ready(params)):
        if not anthropic_key:
            st.error("Anthropic API key required — add it in the sidebar."); st.stop()
        client = anthropic.Anthropic(api_key=anthropic_key, timeout=600.0)
        total_hint = (len(params["companies"]) or max_accounts) * contacts_per
        on_event, done = _make_progress(total_hint)
        leads, costs = [], []
        try:
            if params["companies"]:
                for co in params["companies"]:
                    res = engine.find_leads(
                        client, KEYS, profile, company_name=co["name"], url=co["url"],
                        market=params["market"], titles=titles or None,
                        contacts_per_company=contacts_per, use_builtwith=use_builtwith,
                        on_event=on_event)
                    leads += res["leads"]; costs.append(res["cost"])
            else:
                res = engine.find_leads(
                    client, KEYS, profile,
                    industries=params["industries"], market=params["market"],
                    size_band=params["size_band"], titles=titles or None,
                    max_accounts=max_accounts, contacts_per_company=contacts_per,
                    use_builtwith=use_builtwith, on_event=on_event)
                leads = res["leads"]; costs.append(res["cost"])
        except Exception as exc:
            st.error(f"**Run failed:** {exc}", icon="🚨"); st.stop()
        st.session_state.engine_contacts = leads
        st.session_state.engine_cost     = _merge_costs(costs)
        _save_engine_run(st.session_state.client_key, profile, "contacts",
                         leads, st.session_state.engine_cost)
        done(len(leads))

    if st.session_state.engine_contacts:
        st.markdown("---")
        _cost_caption(st.session_state.engine_cost)
        _render_engine_contacts(st.session_state.engine_contacts, profile)
    elif not _engine_ready(params):
        st.info("Enter at least one industry, or a list of companies, to enable the run.", icon="👈")


def render_engine_previous() -> None:
    profile = st.session_state.active_profile
    client_key = st.session_state.client_key
    if not profile:
        _nav("landing"); return
    _back(to="engine_run_type")
    _breadcrumb("Home", profile["client_name"], "Previous Runs")
    st.title(f"Previous Runs — {profile['client_name']}")
    st.markdown("---")

    runs = _list_engine_runs(client_key)
    if not runs:
        st.info("No previous runs saved for this workspace yet.", icon="📂")
        return

    def _label(r: dict) -> str:
        ts = r["timestamp"]
        pretty = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[9:11]}:{ts[11:13]}" if len(ts) >= 13 else ts
        kind = "📋 Account" if r["mode"] == "accounts" else "👤 Contact"
        return f"{pretty}  ·  {kind} Leads  ·  {r['count']} record(s)"

    idx = st.selectbox(f"{len(runs)} saved run(s)", options=list(range(len(runs))),
                       format_func=lambda i: _label(runs[i]), key="prev_run_pick")
    chosen = runs[idx]

    try:
        data = json.loads(chosen["path"].read_text())
    except Exception as exc:
        st.error(f"Could not load this run: {exc}"); return

    saved_profile = data.get("profile", profile)   # stored profile keeps rendering faithful
    records = data.get("records", [])
    st.caption(f"Loaded `{chosen['path'].name}` · {len(records)} record(s)")
    _cost_caption(data.get("cost"))
    st.markdown("---")

    if data.get("mode") == "accounts":
        _render_engine_accounts(records, saved_profile)
    else:
        _render_engine_contacts(records, saved_profile)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

_routes = {
    "landing":            render_landing,
    "new_run_type":       render_new_run_type,
    "new_run_account":    render_new_run_account,
    "new_run_contact":    render_new_run_contact,
    "view_previous":      render_view_previous,
    # Generic engine
    "generic_profile":    render_generic_profile,
    "engine_run_type":    render_engine_run_type,
    "engine_run_account": render_engine_run_account,
    "engine_run_contact": render_engine_run_contact,
    "engine_previous":    render_engine_previous,
}
_routes.get(st.session_state.page, render_landing)()
