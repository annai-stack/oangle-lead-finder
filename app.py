"""
Oangle Lead Finder — Interactive UI
Run with: streamlit run app.py
"""

import os
import io
import glob
from pathlib import Path

import anthropic
import pandas as pd
import streamlit as st

import lead_finder as lf
import apollo_client

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Oangle Lead Finder",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Styling
# ---------------------------------------------------------------------------

st.markdown("""
<style>
    .score-5 { background:#16a34a; color:white; padding:2px 8px; border-radius:4px; font-weight:700; }
    .score-4 { background:#2563eb; color:white; padding:2px 8px; border-radius:4px; font-weight:700; }
    .score-3 { background:#d97706; color:white; padding:2px 8px; border-radius:4px; font-weight:700; }
    .score-2 { background:#dc2626; color:white; padding:2px 8px; border-radius:4px; font-weight:700; }
    .score-1 { background:#6b7280; color:white; padding:2px 6px; border-radius:4px; font-weight:700; }
    .warm-badge { background:#7c3aed; color:white; padding:2px 6px; border-radius:4px; font-size:0.75em; }
    .grant-badge { background:#0891b2; color:white; padding:2px 6px; border-radius:4px; font-size:0.75em; }
    div[data-testid="stMetricValue"] { font-size:2rem; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Sidebar — Anthropic config
# ---------------------------------------------------------------------------

with st.sidebar:
    st.image("https://placehold.co/180x48/1e293b/ffffff?text=Oangle+Lead+Finder", use_container_width=True)
    st.markdown("---")

    st.subheader("🔑 Anthropic API Key")
    _secret_key = (
        st.secrets.get("ANTHROPIC_API_KEY", "")
        if hasattr(st, "secrets")
        else os.environ.get("ANTHROPIC_API_KEY", "")
    )
    if _secret_key:
        api_key_input = _secret_key
        st.success("API key configured", icon="🔒")
    else:
        api_key_input = st.text_input(
            "Anthropic API Key",
            type="password",
            placeholder="sk-ant-...",
            help="Enter your Anthropic API key. Never stored.",
        )

    st.markdown("---")
    st.subheader("⚙️ Run Config")

    all_segments = lf.TARGET_SEGMENTS
    selected_segments = st.multiselect(
        "Segments to search",
        options=all_segments,
        default=all_segments,
    )
    max_leads      = st.slider("Max leads per segment", 1, 15, lf.MAX_LEADS_PER_SEGMENT)
    min_score      = st.slider("Minimum fit score", 1, 5, lf.MIN_FIT_SCORE)
    require_source = st.checkbox("Require source URL", value=lf.REQUIRE_SOURCE_URL)

    st.markdown("---")
    st.subheader("📋 Lead List Type")
    view_mode = st.radio(
        "Choose what to build",
        ["Account Leads", "Contact Leads"],
        captions=[
            "Company info only — ranked prospect list",
            "Company info + decision-maker contacts from Apollo",
        ],
        label_visibility="collapsed",
    )

    # --- Apollo.io (only relevant for Contact Leads) ---
    _apollo_secret = (
        st.secrets.get("APOLLO_API_KEY", "")
        if hasattr(st, "secrets")
        else os.environ.get("APOLLO_API_KEY", "")
    )
    if view_mode == "Contact Leads":
        st.markdown("---")
        st.subheader("🔗 Apollo.io")
        if _apollo_secret:
            apollo_key = _apollo_secret
            st.success("Apollo key configured", icon="🔒")
        else:
            apollo_key = st.text_input(
                "Apollo API Key",
                type="password",
                placeholder="your-apollo-api-key",
                help="Required for Contact Leads enrichment.",
            )
        max_contacts = st.slider("Max contacts per company", 1, 10, 3)
        title_presets = apollo_client.DEFAULT_TITLES
        contact_titles = st.multiselect(
            "Contact title filter",
            options=title_presets,
            default=title_presets,
            help="Apollo returns only people matching these job titles.",
        )
    else:
        apollo_key     = _apollo_secret
        max_contacts   = 3
        contact_titles = apollo_client.DEFAULT_TITLES

    st.markdown("---")
    st.subheader("🤝 Warm Intro Brands")
    warm_brands_input = st.text_area(
        "One brand per line (case-insensitive)",
        value="\n".join(sorted(lf.WARM_INTRO_BRANDS)),
        height=100,
    )

    run_label = "🚀  Run Account Lead Finder" if view_mode == "Account Leads" else "🚀  Run Contact Lead Finder"
    run_btn = st.button(run_label, type="primary", use_container_width=True)

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.title("🎯 Oangle Lead Finder")
st.caption("AI-powered F&B prospect discovery for Singapore — powered by Claude + web search")
st.markdown("---")

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

for key in ("leads", "contacts_run", "contacts_prev"):
    if key not in st.session_state:
        st.session_state[key] = [] if key == "leads" else pd.DataFrame()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

LEAD_FINDER_DIR = Path(__file__).parent


def _leads_to_df(leads: list[dict]) -> pd.DataFrame:
    rows = []
    for l in leads:
        products = l.get("suggested_products", [])
        rows.append({
            "Brand":   l.get("brand_name", ""),
            "Outlets": l.get("num_outlets_sg", "?"),
            "Score":   l.get("fit_score", ""),
            "Why":     l.get("fit_reason", ""),
            "Products":  ", ".join(products) if isinstance(products, list) else products,
            "Conf":    l.get("confidence", ""),
            "Grant":   l.get("grant_eligible", ""),
            "Warm":    l.get("warm_intro", ""),
            "Website": l.get("website", ""),
            "Source":  l.get("source_url", ""),
            "Segment": l.get("segment", ""),
            "Company": l.get("company_name", ""),
        })
    return pd.DataFrame(rows)


def _csv_to_display_df(path: str | Path) -> pd.DataFrame:
    raw = pd.read_csv(path)
    raw["fit_score"] = pd.to_numeric(raw["fit_score"], errors="coerce")
    return pd.DataFrame({
        "Brand":   raw.get("brand_name", ""),
        "Company": raw.get("company_name", ""),
        "Outlets": raw.get("num_outlets_sg", "?"),
        "Score":   raw["fit_score"],
        "Why":     raw.get("fit_reason", ""),
        "Products":raw.get("suggested_products", ""),
        "Conf":    raw.get("confidence", ""),
        "Grant":   raw.get("grant_eligible", ""),
        "Warm":    raw.get("warm_intro", ""),
        "Website": raw.get("website", ""),
        "Source":  raw.get("source_url", ""),
        "Segment": raw.get("segment", ""),
    })


def _color_score(val):
    colors = {5: "#dcfce7", 4: "#dbeafe", 3: "#fef3c7", 2: "#fee2e2", 1: "#f3f4f6"}
    return f"background-color: {colors.get(val, 'white')}"


def _render_account_leads(leads_df: pd.DataFrame, key: str, download_label: str) -> None:
    """Metrics + filters + styled table + download + lead cards for account-level data."""

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total leads", len(leads_df))
    m2.metric("Score 5 (Ideal)", (leads_df["Score"] == 5).sum())
    m3.metric("Score 4 (Strong)", (leads_df["Score"] == 4).sum())
    m4.metric("Warm intros", (leads_df["Warm"] == "Y").sum())
    m5.metric("Grant eligible", (leads_df["Grant"] == "Y").sum())

    st.markdown("---")

    fc1, fc2, fc3 = st.columns(3)
    with fc1:
        score_filter = st.multiselect("Filter by score", [5, 4, 3, 2, 1], default=[5, 4, 3, 2, 1], key=f"score_{key}")
    with fc2:
        seg_filter = st.multiselect(
            "Filter by segment",
            options=leads_df["Segment"].dropna().unique().tolist(),
            default=leads_df["Segment"].dropna().unique().tolist(),
            key=f"seg_{key}",
        )
    with fc3:
        warm_only  = st.checkbox("Warm intros only", key=f"warm_{key}")
        grant_only = st.checkbox("Grant eligible only", key=f"grant_{key}")

    filtered = leads_df[leads_df["Score"].isin(score_filter) & leads_df["Segment"].isin(seg_filter)]
    if warm_only:
        filtered = filtered[filtered["Warm"] == "Y"]
    if grant_only:
        filtered = filtered[filtered["Grant"] == "Y"]

    st.markdown(f"**{len(filtered)} leads shown**")
    styled = filtered.style.map(_color_score, subset=["Score"])
    st.dataframe(styled, use_container_width=True, hide_index=True, height=480)

    st.markdown("---")
    csv_buf = io.StringIO()
    filtered.to_csv(csv_buf, index=False)
    st.download_button(
        download_label,
        data=csv_buf.getvalue(),
        file_name=f"oangle_account_leads_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv",
        mime="text/csv",
        use_container_width=True,
    )

    st.markdown("---")
    st.subheader("📋 Lead Cards")
    for _, row in filtered.iterrows():
        score = row["Score"]
        warm_html  = '<span class="warm-badge">🤝 Warm intro</span>'  if row["Warm"]  == "Y" else ""
        grant_html = '<span class="grant-badge">💰 Grant eligible</span>' if row["Grant"] == "Y" else ""
        with st.expander(f"{row['Brand']}  —  {row['Outlets']} outlets  ·  Score {score}/5"):
            st.markdown(f'<span class="score-{score}">Score {score}/5</span> {warm_html} {grant_html}', unsafe_allow_html=True)
            c1, c2 = st.columns(2)
            with c1:
                st.markdown(f"**Company:** {row['Company']}")
                st.markdown(f"**Segment:** {row['Segment']}")
                st.markdown(f"**Outlets:** {row['Outlets']}")
                st.markdown(f"**Confidence:** {row['Conf']}")
            with c2:
                st.markdown(f"**Products:** {row['Products']}")
                if row.get("Website"):
                    st.markdown(f"**Website:** [{row['Website']}]({row['Website']})")
                if row.get("Source"):
                    st.markdown(f"**Source:** [{row['Source']}]({row['Source']})")
            st.markdown(f"**Fit reason:** {row['Why']}")


def _render_contact_leads(
    leads_df: pd.DataFrame,
    contacts_key: str,
) -> None:
    """Contact enrichment UI: company selector → fetch → display + download."""

    if not apollo_key:
        st.warning(
            "Apollo API key not found. Switch **Lead List Type → Contact Leads** in the sidebar to enter your key, "
            "or add `APOLLO_API_KEY` to your Streamlit secrets.",
            icon="🔑",
        )
        return

    brand_options = leads_df["Brand"].dropna().unique().tolist()

    # Fetch controls (manual re-fetch or first fetch for Previous Runs)
    with st.expander("🔍 Fetch / re-fetch contacts", expanded=st.session_state[contacts_key].empty):
        select_all = st.checkbox("Select all companies", value=True, key=f"all_{contacts_key}")
        if select_all:
            selected_brands = brand_options
            st.caption(f"{len(brand_options)} companies selected")
        else:
            selected_brands = st.multiselect(
                "Choose companies",
                options=brand_options,
                default=brand_options[:3] if len(brand_options) >= 3 else brand_options,
                key=f"brands_{contacts_key}",
            )

        fetch_btn = st.button(
            f"🔍  Fetch contacts for {len(selected_brands) if selected_brands else 0} {'company' if len(selected_brands) == 1 else 'companies'}",
            key=f"fetch_{contacts_key}",
            type="primary",
            disabled=not selected_brands,
        )

        if fetch_btn and selected_brands:
            progress_bar = st.progress(0, text="Starting Apollo enrichment…")

            def _progress(i, total, brand):
                progress_bar.progress(i / total, text=f"Fetching: **{brand}** ({i + 1}/{total})")

            result_df = apollo_client.enrich_leads(
                api_key=apollo_key,
                leads_df=leads_df,
                brand_filter=selected_brands,
                titles=contact_titles or None,
                max_per_company=max_contacts,
                progress_callback=_progress,
            )
            progress_bar.progress(1.0, text=f"✅ Done — {len(result_df)} rows fetched")
            st.session_state[contacts_key] = result_df

    contacts_df: pd.DataFrame = st.session_state[contacts_key]

    if contacts_df.empty:
        st.info("Click **Fetch contacts** above to pull Apollo data.", icon="👆")
        return

    st.markdown("---")

    # --- Metrics ---
    total_contacts = len(contacts_df[contacts_df["Contact Name"].str.startswith("[") == False])
    has_email = (contacts_df["Email"].str.len() > 0).sum()
    has_phone = (contacts_df["Phone"].str.len() > 0).sum()
    companies_covered = contacts_df["Brand"].nunique()

    cm1, cm2, cm3, cm4 = st.columns(4)
    cm1.metric("Companies covered", companies_covered)
    cm2.metric("Contacts found", total_contacts)
    cm3.metric("With email", int(has_email))
    cm4.metric("With phone", int(has_phone))

    st.markdown("---")

    # --- Filter by company ---
    company_filter = st.multiselect(
        "Filter by company",
        options=contacts_df["Brand"].unique().tolist(),
        default=contacts_df["Brand"].unique().tolist(),
        key=f"cf_brand_{contacts_key}",
    )
    display_df = contacts_df[contacts_df["Brand"].isin(company_filter)]
    st.markdown(f"**{len(display_df)} rows shown**")

    # --- Table ---
    contact_cols = ["Brand", "Company", "Score", "Outlets", "Segment",
                    "Contact Name", "Contact Title", "Email", "Phone", "LinkedIn",
                    "Suggested Products", "Grant Eligible", "Warm Intro", "Website"]
    show_cols = [c for c in contact_cols if c in display_df.columns]
    st.dataframe(display_df[show_cols], use_container_width=True, hide_index=True, height=480)

    st.markdown("---")

    # --- Download ---
    dl1, dl2 = st.columns(2)
    with dl1:
        buf_all = io.StringIO()
        contacts_df.to_csv(buf_all, index=False)
        st.download_button(
            "⬇️  Download ALL contacts (full list)",
            data=buf_all.getvalue(),
            file_name=f"oangle_contact_leads_all_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with dl2:
        buf_sel = io.StringIO()
        display_df.to_csv(buf_sel, index=False)
        st.download_button(
            f"⬇️  Download selected ({len(company_filter)} companies)",
            data=buf_sel.getvalue(),
            file_name=f"oangle_contact_leads_selected_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    # --- Contact cards ---
    st.markdown("---")
    st.subheader("👤 Contact Cards")
    for brand, group in display_df.groupby("Brand"):
        row0 = group.iloc[0]
        score = row0.get("Score", "")
        with st.expander(f"{brand}  —  Score {score}/5  ·  {len(group)} contact(s)"):
            for _, cr in group.iterrows():
                name  = cr["Contact Name"]
                title = cr.get("Contact Title", "")
                email = cr.get("Email", "")
                phone = cr.get("Phone", "")
                li    = cr.get("LinkedIn", "")
                st.markdown(f"**{name}** — {title}")
                cols = st.columns(3)
                if email:
                    cols[0].markdown(f"📧 {email}")
                if phone:
                    cols[1].markdown(f"📞 {phone}")
                if li:
                    cols[2].markdown(f"[LinkedIn]({li})")
                st.markdown("---")


def _render_results(
    leads_df: pd.DataFrame,
    contacts_key: str,
    key: str,
    account_download_label: str,
    mode: str = "Account Leads",
) -> None:
    """Render Account Leads or Contact Leads depending on sidebar mode selection."""
    if mode == "Account Leads":
        _render_account_leads(leads_df, key=key, download_label=account_download_label)
    else:
        _render_contact_leads(leads_df, contacts_key=contacts_key)


# ---------------------------------------------------------------------------
# Run logic
# ---------------------------------------------------------------------------

if run_btn:
    if not api_key_input:
        st.error("Please enter your Anthropic API key in the sidebar.")
        st.stop()
    if not selected_segments:
        st.error("Please select at least one segment.")
        st.stop()

    lf.MAX_LEADS_PER_SEGMENT = max_leads
    lf.MIN_FIT_SCORE         = min_score
    lf.REQUIRE_SOURCE_URL    = require_source
    lf.WARM_INTRO_BRANDS     = {b.strip().lower() for b in warm_brands_input.splitlines() if b.strip()}

    client = anthropic.Anthropic(api_key=api_key_input)
    st.session_state.leads = []
    st.session_state.contacts_run = pd.DataFrame()

    progress_bar = st.progress(0, text="Starting…")
    results_placeholder = st.empty()
    all_raw: list[dict] = []

    for i, segment in enumerate(selected_segments):
        progress_bar.progress(i / len(selected_segments), text=f"Searching: **{segment}** ({i+1}/{len(selected_segments)})")
        with st.spinner(f"Searching {segment}…"):
            leads = lf.search_segment(client, segment)
            all_raw.extend(leads)

        processed = lf.post_process(list(all_raw))
        st.session_state.leads = processed
        if processed:
            results_placeholder.dataframe(_leads_to_df(processed), use_container_width=True, hide_index=True)

    progress_bar.progress(1.0, text=f"✅ Step 1 done — {len(st.session_state.leads)} unique leads found")

    # Auto-enrich with Apollo when Contact Leads mode is selected
    if view_mode == "Contact Leads":
        if not apollo_key:
            st.warning("Apollo API key not configured — skipping contact enrichment.", icon="🔑")
        else:
            results_placeholder.empty()
            leads_df_run = _leads_to_df(st.session_state.leads)
            apollo_bar = st.progress(0, text="Step 2: Fetching contacts from Apollo…")

            def _run_progress(i, total, brand):
                apollo_bar.progress(i / total, text=f"Apollo: **{brand}** ({i + 1}/{total})")

            st.session_state.contacts_run = apollo_client.enrich_leads(
                api_key=apollo_key,
                leads_df=leads_df_run,
                brand_filter=None,
                titles=contact_titles or None,
                max_per_company=max_contacts,
                progress_callback=_run_progress,
            )
            apollo_bar.progress(1.0, text=f"✅ Done — contacts fetched for {leads_df_run['Brand'].nunique()} companies")

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------

tab_run, tab_prev = st.tabs(["🚀 New Run", "📁 Previous Runs"])

# --- Tab 1: New Run ---
with tab_run:
    if st.session_state.leads:
        st.caption(f"Showing results as: **{view_mode}**")
        st.markdown("---")
        _render_results(
            leads_df=_leads_to_df(st.session_state.leads),
            contacts_key="contacts_run",
            key="run",
            account_download_label="⬇️  Download Account Leads",
            mode=view_mode,
        )
    else:
        st.info(
            f"Select **{view_mode}** in the sidebar and click **{('Run Account Lead Finder' if view_mode == 'Account Leads' else 'Run Contact Lead Finder')}** to start.",
            icon="👈",
        )
        col1, col2 = st.columns(2)
        with col1:
            st.markdown("""
**Account Leads**
- Ranked list of F&B companies
- Outlet count, fit score, segment
- Grant eligible & warm intro flags
- Ready for outreach as-is
            """)
        with col2:
            st.markdown("""
**Contact Leads**
- Everything in Account Leads, plus:
- Decision-maker name & title
- Work email & phone
- LinkedIn URL (via Apollo.io)
            """)

# --- Tab 2: Previous Runs ---
with tab_prev:
    csv_files = sorted(
        LEAD_FINDER_DIR.glob("leads_fnb_*.csv"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    if not csv_files:
        st.info("No previous runs found in the lead-finder directory.", icon="📂")
    else:
        pcol1, pcol2 = st.columns([3, 1])
        with pcol1:
            file_labels   = [p.name for p in csv_files]
            selected_label = st.selectbox(f"Select a run ({len(csv_files)} available)", options=file_labels)
        with pcol2:
            prev_mode = st.radio(
                "View as",
                ["Account Leads", "Contact Leads"],
                key="prev_mode",
            )

        selected_path = LEAD_FINDER_DIR / selected_label

        try:
            prev_df = _csv_to_display_df(selected_path)
            st.caption(f"Loaded: `{selected_label}`  ·  {len(prev_df)} leads")
            st.markdown("---")
            _render_results(
                leads_df=prev_df,
                contacts_key="contacts_prev",
                key=f"prev_{selected_label}",
                account_download_label=f"⬇️  Download {selected_label}",
                mode=prev_mode,
            )
        except Exception as exc:
            st.error(f"Could not load file: {exc}")
