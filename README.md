# AI Lead Insights — Oangle Lead Finder

Generates a research-backed, contact-level lead spreadsheet (the 23-column
"AI Lead Insights" format) for a client's outbound sales. Built for Oangle
(Singapore restaurant tech); reusable for other clients via `client_config.py`.

---

## Inputs required

### 1. API keys — `.streamlit/secrets.toml` (gitignored)

| Key | Required? | Used for | Notes |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | **Yes** (for AI columns) | Company + contact enrichment, premium polish | Pay-as-you-go credits must be funded |
| `HUNTER_API_KEY` | **Yes** (for contacts) | Real contact name / email / LinkedIn / seniority | Primary contact source |
| `APOLLO_API_KEY` | **Yes** (for firmographics) | Company size, industry, location | People-search is masked on lower tiers; org-enrich works |
| `BUILTWITH_API_KEY` | Optional | Greenfield-vs-competitor tech signal | Free tier = coarse signal; Domain API (paid) = vendor names |
| `SUPABASE_URL` + `SUPABASE_KEY` | Optional | Persistence + caching (see below) | Omit → pipeline runs file-only, unchanged. Use the **service-role** key (backend only) |

### 2. Company source (pick one)
- **Existing CSV** — `--from-latest-csv` or `--from-csv path` (reuses a prior
  `leads_fnb_*.csv`; free, no web search).
- **Fresh web search** — default; runs the segment search in `lead_finder.py`
  (costs Anthropic web-search calls).

### 3. Run configuration (CLI flags on `run_insights.py`)

| Flag | Default | Meaning |
|---|---|---|
| `--client` | `oangle` | Client profile in `client_config.py` |
| `--max-companies` | 10 | Cap companies sourced |
| `--contacts-per-company` | 3 | Contacts pulled per company |
| `--max-enrich` | 0 (no cap) | Hard cap on total contacts enriched (cheap smoke tests) |
| `--source` | `hunter` | Contact source: hunter / apollo / both |
| `--titles` | profile default | Comma-separated job-title keywords — only contacts whose title matches are kept (e.g. `owner,operations,IT,director,procurement`) |
| `--seniority` | (none) | Comma-separated seniority keywords — Apollo: owner,c_suite,vp,head,director,manager,senior · Hunter: executive,senior,junior |
| `--model` | `claude-sonnet-4-6` | Draft model (cost lever) |
| `--premium-model` | `claude-opus-4-8` | Polishes top-tier outreach |
| `--premium-heat` | `1A,1B` | Heat ranks that get the premium polish |
| `--batch` | off | Run via Batches API (50% cheaper, async) |
| `--no-builtwith` | off | Skip technographics |
| `--no-verify` | off | Skip the verification pass (verification is **on** by default) |
| `--no-verify-claims` | off | Within verification, skip the model claim cross-check (still validates emails + source URLs) |
| `--no-db` | off | Disable Supabase persistence/caching even if configured |
| `--fresh` | off | Bypass enrichment-cache **reads** (regenerate + refresh). Use after changing prompts |
| `--include-contacted` | off | Don't skip leads already logged as contacted (e.g. a follow-up campaign) |
| `--apollo-probe DOMAIN` | — | Verify Apollo wiring only, then exit |

### 4. Client profile — `client_config.py`
Value proposition, ICP, target job titles, scoring logic, heat-rank tiers.
Adding a new client = one new dict entry.

---

## Output provided

### 1. The spreadsheet — `AI_Lead_Insights_<client>_<timestamp>.xlsx`
23 columns, one row per contact, in the reference layout:

- **From Apollo/Hunter (factual):** Company Domain, First/Last Name, LinkedIn,
  Email, Company Size, Job Title, Seniority, Industry, Location, Company Name.
- **AI-researched (Anthropic + web search):** Account Topline Description,
  Latest Press Releases, How to Target the Company, Position & Title, Past 3
  Work Experience, Person-level Press, CVP vs Lead's Profile, Personalised
  Sales Paragraph, Proposed Heat Rank.
- **Meta:** Sample Scoring Logic (the rubric used).
- **Account context (client-specific, appended):** Outlets (SG), Account Fit
  Score, Suggested Products, Grant Eligible, Warm Intro, Segment, Account Fit
  Reason, Account Source URL — carried from the company-discovery stage. Defined
  per client via `account_columns` in `client_config.py`.
- Inbound-only columns (Interest/Topic, Message From Lead) are blank for
  outbound clients.

Rows sorted by heat rank (1A → 4).

### 2. Records JSON — `_insights_records.json`
The same records, consumed by the local viewer.

### 3. Local viewer — `insights_app.py` (Streamlit)
Heat-rank metrics, filters, table, lead cards (full enrichment + live BuiltWith
signal), and an Excel download. Displays pre-generated results — no Anthropic
key needed to view.

### 4. Verification (on by default — `verify.py`)
Turns the model's self-reported claims into **checkable** signals before export:
- **Email** — Hunter email-verifier marks each contact `deliverable` / `risky` /
  `undeliverable` (never silently ship an undeliverable address).
- **Source URL** — confirms the discovery `source_url` actually resolves (HTTP 200).
- **Claim cross-check** — one cheap, no-web-search model call per company asks
  whether that page supports the asserted company + outlet count.

These combine into a real **`Data Confidence`** column (high / med / low) — *not*
the model's self-reported confidence — plus an `Email Status` and
`Verification Notes` column. Rows flagged `needs_review` (low confidence or
undeliverable email) are also written to a separate **`Review`** sheet so reps
triage them before sending. Disable with `--no-verify` (or `--no-verify-claims`
to keep email/URL checks but skip the model call).

### 5. Persistence + caching (optional — `db.py`, Supabase)
**Off by default and fully optional** — when `SUPABASE_URL`/`SUPABASE_KEY` are
absent, `db.py` returns a no-op store and the pipeline behaves exactly as before.
When configured (run `supabase_schema.sql` once to provision), it cuts cost and
latency on reruns:
- **Enrichment cache** — Claude company/contact/polish results are reused
  instead of regenerated (the big token saver). Keyed by entity + a
  `PROMPT_VERSION` so changing a prompt cleanly invalidates old entries; freshness
  TTLs (company 45d, contact/polish 90d) refuse stale hits.
- **Firmographics cache** — Apollo org-enrich results reused by domain (saves
  Apollo credits).
- **Outreach dedup** — every exported contact is logged; future runs skip
  already-worked leads so you spend nothing re-enriching them (`--include-contacted`
  to override).
- **Provenance** — one `runs` row per invocation (args, counts, cost).

`pip install supabase` (already in `requirements.txt`). See `DESIGN_persistence.md`
for the schema and rollout plan.

### 6. Per-run cost report (printed)
Per-stage token-fee breakdown (company / contact / premium polish), priced per
model, with the Batch API discount applied when `--batch` is used, plus a
per-lead average.

---

## Typical commands

```bash
cd path/to/lead-finder

# Verify Apollo (one call, no enrichment)
.venv/bin/python run_insights.py --apollo-probe kfc.com.sg

# Cheap smoke test (2 contacts)
.venv/bin/python run_insights.py --from-latest-csv --max-companies 2 \
    --contacts-per-company 1 --max-enrich 2

# Full run, cost-optimised (Sonnet draft + Opus polish on 1A/1B, batch)
.venv/bin/python run_insights.py --from-latest-csv --max-companies 10 \
    --contacts-per-company 3 --batch
```

### Running the UI — there are TWO apps, and a demo needs both

```bash
# Main UI: landing → Select a Client / Generic Engine / Previous Runs
.venv/bin/streamlit run app.py                              # localhost:8501

# Insights viewer: enriched contact cards + xlsx download
.venv/bin/streamlit run insights_app.py --server.port 8502  # localhost:8502
```

`app.py` is the current entry point. `insights_app.py` is the older single
screen, still used for the enriched contact cards — **start both before a
demo call**. Neither is deployed anywhere; this is laptop-only, so nobody at
the client can reach it. See `DEMO_GUIDE.md` for the runbook.

## Approximate cost (Anthropic only; Apollo/Hunter/BuiltWith billed separately)
- Opus, naive: ~$0.22/lead
- Sonnet draft + Opus polish (default): ~$0.12/lead *modelled*
- + `--batch`: ~$0.07/lead *modelled* (500 leads ≈ $37)

> **Measured, not modelled (22 Jul 2026):** a real end-to-end run cost
> **~$0.197/lead** — company enrichment burns ~6 web searches and ~8.4k output
> tokens, more than the model above assumes. Quote the measured figure to
> clients. The `--batch` discount has only ever been tested against mocks,
> never live.

## ⚠️ Timing — do not run live in front of a prospect

A real run took **8 min 58 s for 2 leads** (~4.5 min/lead). 10 leads ≈ 45
minutes; the deck's "100+ leads per run" is ≈ 7.5 hours. This is an overnight
batch job. The honest pitch is *"queue it, list waiting next morning"* — never
"live in 10 minutes". Demo from the pre-baked June set instead (toggle **Show
previously generated leads** in the 8502 sidebar).
