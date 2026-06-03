"""
Oangle Lead Finder — Interactive UI
Run with: streamlit run app.py
"""

import os
import io
import anthropic
import pandas as pd
import streamlit as st

import lead_finder as lf

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
    /* Score badge colours */
    .score-5 { background:#16a34a; color:white; padding:2px 8px; border-radius:4px; font-weight:700; }
    .score-4 { background:#2563eb; color:white; padding:2px 8px; border-radius:4px; font-weight:700; }
    .score-3 { background:#d97706; color:white; padding:2px 8px; border-radius:4px; font-weight:700; }
    .score-2 { background:#dc2626; color:white; padding:2px 8px; border-radius:4px; font-weight:700; }
    .score-1 { background:#6b7280; color:white; padding:2px 8px; border-radius:4px; font-weight:700; }
    .warm-badge { background:#7c3aed; color:white; padding:2px 6px; border-radius:4px; font-size:0.75em; }
    .grant-badge { background:#0891b2; color:white; padding:2px 6px; border-radius:4px; font-size:0.75em; }
    div[data-testid="stMetricValue"] { font-size:2rem; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Sidebar — configuration
# ---------------------------------------------------------------------------

with st.sidebar:
    st.image("https://placehold.co/180x48/1e293b/ffffff?text=Oangle+Lead+Finder", use_container_width=True)
    st.markdown("---")

    st.subheader("🔑 API Key")
    # Resolve API key: Streamlit secrets → env var → user input
    _default_key = (
        st.secrets.get("ANTHROPIC_API_KEY", "")
        if hasattr(st, "secrets")
        else os.environ.get("ANTHROPIC_API_KEY", "")
    )
    api_key_input = st.text_input(
        "Anthropic API Key",
        type="password",
        value=_default_key,
        placeholder="sk-ant-...",
        help="Pre-filled from Streamlit secrets if configured. Never stored.",
    )

    st.markdown("---")
    st.subheader("⚙️ Run Config")

    all_segments = lf.TARGET_SEGMENTS
    selected_segments = st.multiselect(
        "Segments to search",
        options=all_segments,
        default=all_segments,
    )

    max_leads = st.slider("Max leads per segment", 1, 15, lf.MAX_LEADS_PER_SEGMENT)
    min_score = st.slider("Minimum fit score", 1, 5, lf.MIN_FIT_SCORE)
    require_source = st.checkbox("Require source URL", value=lf.REQUIRE_SOURCE_URL)

    st.markdown("---")
    st.subheader("🤝 Warm Intro Brands")
    warm_brands_input = st.text_area(
        "One brand per line (case-insensitive)",
        value="\n".join(sorted(lf.WARM_INTRO_BRANDS)),
        height=100,
    )

    run_btn = st.button("🚀  Run Lead Finder", type="primary", use_container_width=True)

# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

st.title("🎯 Oangle Lead Finder")
st.caption("AI-powered F&B prospect discovery for Singapore — powered by Claude + web search")
st.markdown("---")

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------

if "leads" not in st.session_state:
    st.session_state.leads = []
if "running" not in st.session_state:
    st.session_state.running = False

# ---------------------------------------------------------------------------
# Helper (must be defined before first use)
# ---------------------------------------------------------------------------


def _leads_to_df(leads: list[dict]) -> pd.DataFrame:
    rows = []
    for l in leads:
        products = l.get("suggested_products", [])
        rows.append({
            "Brand": l.get("brand_name", ""),
            "Outlets": l.get("num_outlets_sg", "?"),
            "Score": l.get("fit_score", ""),
            "Why": l.get("fit_reason", ""),
            "Products": ", ".join(products) if isinstance(products, list) else products,
            "Conf": l.get("confidence", ""),
            "Grant": l.get("grant_eligible", ""),
            "Warm": l.get("warm_intro", ""),
            "Website": l.get("website", ""),
            "Source": l.get("source_url", ""),
            "Segment": l.get("segment", ""),
            "Company": l.get("company_name", ""),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if run_btn:
    if not api_key_input:
        st.error("Please enter your Anthropic API key in the sidebar.")
        st.stop()
    if not selected_segments:
        st.error("Please select at least one segment.")
        st.stop()

    # Apply config overrides for this run
    lf.MAX_LEADS_PER_SEGMENT = max_leads
    lf.MIN_FIT_SCORE = min_score
    lf.REQUIRE_SOURCE_URL = require_source
    lf.WARM_INTRO_BRANDS = {b.strip().lower() for b in warm_brands_input.splitlines() if b.strip()}

    client = anthropic.Anthropic(api_key=api_key_input)
    st.session_state.leads = []

    progress_bar = st.progress(0, text="Starting…")
    status_col, _ = st.columns([3, 1])
    results_placeholder = st.empty()
    all_raw: list[dict] = []

    for i, segment in enumerate(selected_segments):
        progress_bar.progress(
            (i) / len(selected_segments),
            text=f"Searching: **{segment}** ({i+1}/{len(selected_segments)})",
        )
        with st.spinner(f"Searching {segment}…"):
            leads = lf.search_segment(client, segment)
            all_raw.extend(leads)

        processed = lf.post_process(list(all_raw))
        st.session_state.leads = processed

        # Live preview table
        if processed:
            results_placeholder.dataframe(
                _leads_to_df(processed),
                use_container_width=True,
                hide_index=True,
            )

    progress_bar.progress(1.0, text=f"✅ Done — {len(st.session_state.leads)} unique leads")


# ---------------------------------------------------------------------------
# Results display
# ---------------------------------------------------------------------------

if st.session_state.leads:
    leads = st.session_state.leads
    df = _leads_to_df(leads)

    # --- Metrics row ---
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total leads", len(leads))
    m2.metric("Score 5 (Ideal)", sum(1 for l in leads if l.get("fit_score") == 5))
    m3.metric("Score 4 (Strong)", sum(1 for l in leads if l.get("fit_score") == 4))
    m4.metric("Warm intros", sum(1 for l in leads if l.get("warm_intro") == "Y"))
    m5.metric("Grant eligible", sum(1 for l in leads if l.get("grant_eligible") == "Y"))

    st.markdown("---")

    # --- Filters ---
    fc1, fc2, fc3 = st.columns(3)
    with fc1:
        score_filter = st.multiselect(
            "Filter by score", [5, 4, 3, 2, 1],
            default=[5, 4, 3, 2, 1],
        )
    with fc2:
        seg_filter = st.multiselect(
            "Filter by segment",
            options=df["Segment"].unique().tolist(),
            default=df["Segment"].unique().tolist(),
        )
    with fc3:
        warm_only = st.checkbox("Warm intros only")
        grant_only = st.checkbox("Grant eligible only")

    filtered = df[df["Score"].isin(score_filter) & df["Segment"].isin(seg_filter)]
    if warm_only:
        filtered = filtered[filtered["Warm"] == "Y"]
    if grant_only:
        filtered = filtered[filtered["Grant"] == "Y"]

    st.markdown(f"**{len(filtered)} leads shown**")

    # --- Styled table ---
    def color_score(val):
        colors = {5: "#dcfce7", 4: "#dbeafe", 3: "#fef3c7", 2: "#fee2e2", 1: "#f3f4f6"}
        return f"background-color: {colors.get(val, 'white')}"

    styled = filtered.style.applymap(color_score, subset=["Score"])
    st.dataframe(styled, use_container_width=True, hide_index=True, height=500)

    # --- Export ---
    st.markdown("---")
    exp1, exp2 = st.columns(2)

    with exp1:
        csv_buf = io.StringIO()
        full_df = _leads_to_df(leads)
        full_df.to_csv(csv_buf, index=False)
        st.download_button(
            "⬇️  Download CSV",
            data=csv_buf.getvalue(),
            file_name=f"oangle_leads_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
            use_container_width=True,
        )

    with exp2:
        st.info("Google Sheets export — run `python lead_finder.py` then use the MCP integration in Claude Code.", icon="📊")

    # --- Lead cards (expandable) ---
    st.markdown("---")
    st.subheader("📋 Lead Cards")

    for _, row in filtered.iterrows():
        score = row["Score"]
        badge_class = f"score-{score}"
        warm_html = '<span class="warm-badge">🤝 Warm intro</span>' if row["Warm"] == "Y" else ""
        grant_html = '<span class="grant-badge">💰 Grant eligible</span>' if row["Grant"] == "Y" else ""

        with st.expander(f"{row['Brand']}  —  {row['Outlets']} outlets  ·  Score {score}/5"):
            st.markdown(
                f'<span class="{badge_class}">Score {score}/5</span> {warm_html} {grant_html}',
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
                if row["Website"]:
                    st.markdown(f"**Website:** [{row['Website']}]({row['Website']})")
                if row["Source"]:
                    st.markdown(f"**Source:** [{row['Source']}]({row['Source']})")
            st.markdown(f"**Fit reason:** {row['Why']}")

else:
    st.info("Configure your run in the sidebar and click **Run Lead Finder** to start.", icon="👈")
    st.markdown("""
    **What this tool does:**
    - Searches the web for F&B chains in Singapore across 6 segments
    - Scores each prospect 1–5 against Oangle's ideal customer profile
    - Flags warm intros (Subway, KFC, Guzman) and grant-eligible leads
    - Exports a ranked CSV ready for the sales team

    **Output fields:** Brand · Company · Outlets · Website · Source URL · Fit Score · Fit Reason · Confidence · Grant Eligible · Warm Intro · Suggested Products · Segment
    """)
