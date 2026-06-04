"""
Oangle Lead Finder — Interactive UI
Run with: streamlit run app.py
"""

import os
import io
from pathlib import Path

import anthropic
import pandas as pd
import streamlit as st

import lead_finder as lf
import apollo_client

# ---------------------------------------------------------------------------
# Page config + styling
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Oangle Lead Finder",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

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

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

_defaults: dict = {
    "page":     "landing",
    "leads":    [],
    "contacts": pd.DataFrame(),
}
for _k, _v in _defaults.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ---------------------------------------------------------------------------
# Sidebar — API keys (always visible) + page-specific config
# ---------------------------------------------------------------------------

with st.sidebar:
    st.image(
        "https://placehold.co/180x48/1e293b/ffffff?text=Oangle+Lead+Finder",
        use_container_width=True,
    )
    st.markdown("---")

    # Anthropic
    _anth_secret = (
        st.secrets.get("ANTHROPIC_API_KEY", "")
        if hasattr(st, "secrets")
        else os.environ.get("ANTHROPIC_API_KEY", "")
    )
    if _anth_secret:
        anthropic_key = _anth_secret
        st.success("Anthropic key configured", icon="🔒")
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
        st.success("Apollo key configured", icon="🔒")
    else:
        apollo_key = st.text_input("🔗 Apollo API Key", type="password", placeholder="apollo-key...")

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

    # Contact Leads Apollo config
    if _page == "new_run_contact":
        st.markdown("---")
        st.subheader("⚙️ Apollo Config")
        max_contacts   = st.slider("Max contacts per company", 1, 10, 3)
        contact_titles = st.multiselect(
            "Title filter",
            options=apollo_client.DEFAULT_TITLES,
            default=apollo_client.DEFAULT_TITLES,
        )
    else:
        max_contacts   = 3
        contact_titles = apollo_client.DEFAULT_TITLES

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
    m4.metric("Warm intros", (df["Warm"] == "Y").sum())
    m5.metric("Grant eligible", (df["Grant"] == "Y").sum())
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
    st.title("🎯 Oangle Lead Finder")
    st.caption("AI-powered F&B prospect discovery for Singapore — powered by Claude + Apollo")
    st.markdown("---")

    csv_count = len(list(LEAD_FINDER_DIR.glob("leads_fnb_*.csv")))

    col1, col2 = st.columns(2, gap="large")
    with col1:
        st.markdown("### 🚀 New Run")
        st.markdown(
            "Run a new web search for F&B leads and/or enrich companies with Apollo contact data."
        )
        st.markdown(" ")
        if st.button("Start New Run →", type="primary", use_container_width=True):
            _nav("new_run_type")
    with col2:
        st.markdown("### 📁 View Previous Runs")
        st.markdown(
            f"Browse, filter and download past lead outputs. **{csv_count} run(s)** saved."
        )
        st.markdown(" ")
        if st.button("View Previous Runs →", use_container_width=True, disabled=csv_count == 0):
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
Enrich an existing account leads list with decision-maker contact data from Apollo.io.

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
            with st.spinner(f"Searching {segment}…"):
                all_raw.extend(lf.search_segment(client, segment))
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
    st.caption("Enrich companies with Apollo.io contact data.")
    st.markdown("---")

    if not apollo_key:
        st.warning("Apollo API key required — add it in the sidebar.", icon="🔑")
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
        st.caption("Apollo will search by company name keyword. One name per line.")
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
            progress_bar = st.progress(0, text="Starting Apollo enrichment…")

            def _progress(i: int, total: int, brand: str) -> None:
                progress_bar.progress(i / total, text=f"Fetching: **{brand}** ({i+1}/{total})")

            st.session_state.contacts = apollo_client.enrich_leads(
                api_key=apollo_key,
                leads_df=account_df_source,
                brand_filter=selected_brands,
                titles=contact_titles or None,
                max_per_company=max_contacts,
                progress_callback=_progress,
                use_keyword=use_keyword,
            )
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


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

_routes = {
    "landing":          render_landing,
    "new_run_type":     render_new_run_type,
    "new_run_account":  render_new_run_account,
    "new_run_contact":  render_new_run_contact,
    "view_previous":    render_view_previous,
}
_routes.get(st.session_state.page, render_landing)()
