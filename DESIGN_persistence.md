# Design — Supabase persistence + dedup

Status: **v1 implemented** (`db.py`, `supabase_schema.sql`, wired into the
pipeline behind optional `SUPABASE_URL`/`SUPABASE_KEY`). This doc is the design
of record; the notes below describe the shipped behaviour. Scope: P1 from the
product review.

**What shipped vs. this design:** the `org` firmographics cache reuses the
`enrichment_cache` table (`kind='org'`) rather than a column on `companies`, so
no 6th table was needed. Batch-path caching is deferred (see §8). Everything
else below matches the implementation.
Goal: stop re-paying for the same enrichment, stop contacting the same lead
twice, and give the pipeline a durable memory so it can run hosted (not just on
one laptop writing local files).

---

## 1. Why (the three concrete problems this solves)

| Problem today | Cause | What persistence fixes |
|---|---|---|
| **Re-paying for enrichment** | Every run re-calls Claude + Apollo/Hunter for companies it already researched | Cache enrichment by company/contact; reuse if fresh |
| **Double-contacting leads** | Nothing remembers who was already pulled or emailed | An `outreach` log + dedup on company/contact identity |
| **Can't run hosted** | Results live in local CSV/JSON files | A shared DB any deployment can read/write |

Non-goals (for this phase): auth/multi-tenant UI, a full CRM, replacing the
Excel export. The Excel/JSON outputs stay; the DB sits *underneath* them.

---

## 2. Why Supabase specifically

- It's **Postgres** — real SQL, joins, unique constraints, `jsonb` for the
  flexible enrichment blob. No lock-in beyond standard Postgres.
- One managed service gives **DB + REST + auth + Python client**, so when we
  later let Oangle log in (the other P1), the auth is already there.
- Free tier covers an MVP at this volume (hundreds–thousands of leads).

A plain Postgres (Neon/RDS) would also work; the schema below is portable. If we
*only* ever need caching and never hosting/auth, even SQLite would do — but we
already flagged hosting + login as the roadmap, so Supabase is the right bet.

---

## 3. Schema

Five tables. `companies` and `contacts` are the durable entities;
`enrichment_cache` is the cost-saver; `runs` + `outreach` are the audit/dedup log.

```sql
-- A company, deduped by domain (fallback: normalised name).
create table companies (
  id            uuid primary key default gen_random_uuid(),
  client        text not null,                 -- 'oangle' (matches client_config key)
  domain        text,                           -- canonical, from apollo_client.extract_domain
  name_norm     text not null,                  -- lower(trim(brand_name)) — dedup key when domain missing
  company_name  text,
  brand_name    text,
  segment       text,
  num_outlets_sg int,
  fit_score     int,
  fit_reason    text,
  source_url    text,
  data          jsonb default '{}',             -- full discovery row + firmographics
  created_at    timestamptz default now(),
  updated_at    timestamptz default now(),
  unique (client, domain),                       -- domain dedup (NULLs allowed; see name_norm)
  unique (client, name_norm)                     -- name dedup when domain is null
);

-- A decision-maker at a company, deduped by email (fallback: linkedin / name+company).
create table contacts (
  id            uuid primary key default gen_random_uuid(),
  company_id    uuid references companies(id) on delete cascade,
  client        text not null,
  email         text,
  email_norm    text,                            -- lower(trim(email)) — primary dedup key
  linkedin_url  text,
  first_name    text,
  last_name     text,
  job_title     text,
  seniority     text,
  source        text,                            -- 'Apollo' | 'Hunter'
  -- verification (from verify.py)
  email_result      text,                        -- deliverable/risky/undeliverable
  data_confidence   text,                        -- high/med/low
  verification_notes text,
  data          jsonb default '{}',              -- full enriched record (the export row)
  created_at    timestamptz default now(),
  updated_at    timestamptz default now(),
  unique (client, email_norm)                    -- email dedup
);

-- Cache of expensive enrichment, keyed by entity + a version so prompt changes
-- can invalidate cleanly. Lets a rerun skip the Claude/web-search calls.
create table enrichment_cache (
  id            uuid primary key default gen_random_uuid(),
  client        text not null,
  kind          text not null,                   -- 'company' | 'contact' | 'polish'
  entity_key    text not null,                   -- domain (company) or email_norm (contact)
  prompt_version text not null default 'v1',     -- bump when prompts in insights_enrich change
  payload       jsonb not null,                  -- the enrich_company/enrich_contact dict
  created_at    timestamptz default now(),
  unique (client, kind, entity_key, prompt_version)
);
create index on enrichment_cache (created_at);    -- for freshness TTL queries

-- One row per pipeline invocation (provenance + cost reporting over time).
create table runs (
  id            uuid primary key default gen_random_uuid(),
  client        text not null,
  args          jsonb,                            -- the CLI args used
  n_companies   int,
  n_contacts    int,
  cost_usd      numeric(10,4),
  started_at    timestamptz default now(),
  finished_at   timestamptz
);

-- The dedup-on-outreach log: who has been exported/sent, so future runs skip them.
create table outreach (
  id            uuid primary key default gen_random_uuid(),
  contact_id    uuid references contacts(id) on delete cascade,
  client        text not null,
  run_id        uuid references runs(id),
  status        text not null default 'exported', -- exported | sent | replied | bounced | suppressed
  channel       text,                             -- 'email' | ...
  created_at    timestamptz default now()
);
create index on outreach (contact_id, status);
```

### Identity / dedup keys (the crux)
- **Company:** `domain` if present (canonicalised via the now-fixed
  `extract_domain`), else `name_norm = lower(trim(brand_name))`.
- **Contact:** `email_norm = lower(trim(email))` primary; if email missing, fall
  back to `linkedin_url`, then `name_norm + company_id`.
- Upserts use `on conflict (...) do update` so re-discovering a company refreshes
  its row instead of duplicating it.

---

## 4. Dedup strategy across runs

Two distinct dedup needs:

1. **Entity dedup (don't store the same company/contact twice).**
   Handled by the unique constraints + upsert. A second run that finds KFC again
   updates the existing `companies` row.

2. **Outreach dedup (don't contact someone already worked).**
   Before enrichment, filter out contacts whose `email_norm` already has an
   `outreach` row with status in (`exported`,`sent`,`replied`,`suppressed`).
   Add a `--include-contacted` flag to override (e.g. a follow-up campaign).
   `bounced` deliberately is *not* suppressed from re-discovery but the contact's
   `email_result` will block auto-send via the existing `verify.email_is_sendable`.

---

## 5. Enrichment caching (the cost-saver)

Wrap the two expensive calls in `insights_enrich`:

```
enrich_company(company):
    key = extract_domain(company.website)
    hit = cache_get(client, 'company', key, PROMPT_VERSION, max_age=CACHE_TTL)
    if hit: return hit.payload          # $0, no Claude/web-search call
    result = <existing logic>
    cache_put(client, 'company', key, PROMPT_VERSION, result)
    return result
```

Same pattern for `enrich_contact` (key = `email_norm`).
- **TTL:** company research ages (~30–60 days); contact bios age slower. Make
  `CACHE_TTL` a config value; `--fresh` flag bypasses cache.
- **Invalidation by prompt change:** `PROMPT_VERSION` constant in
  `insights_enrich`. Bump it when prompts change → old cache entries are simply
  never matched (and a periodic job can prune `created_at < now() - retention`).

Expected saving: on reruns of an overlapping company set, Stage 3 (the dominant
cost) drops toward $0 for cache hits. The bigger external win is **not re-paying
Apollo/Hunter credits** — cache firmographics + contacts the same way.

---

## 6. Integration points in the pipeline

Minimal, additive — guarded by a single `--db` flag (or `SUPABASE_URL` presence)
so the file-based path keeps working unchanged when the DB is off.

| Stage | File | Change |
|---|---|---|
| Start | `run_insights.py` | create a `runs` row; open a `db` handle |
| 1 Companies | `run_insights.py` | upsert each company; **skip** outreach-contacted on request |
| 2 Contacts | `run_insights.py` / `apollo_client` | upsert contacts; dedup against existing `outreach` |
| 3 Enrichment | `insights_enrich.py` | cache-get before / cache-put after `enrich_company`, `enrich_contact` |
| 3.5 Verify | `verify.py` | persist `email_result` / `data_confidence` onto the contact row |
| 4 Export | `run_insights.py` | write `outreach` rows (status `exported`) for every shipped contact; finalise `runs` (cost, counts) |
| Viewer | `insights_app.py` | optional: read from DB instead of `_insights_records.json` |

New module: `db.py` — a thin wrapper over `supabase-py` with
`upsert_company`, `upsert_contact`, `cache_get`, `cache_put`,
`already_contacted(email_norm) -> bool`, `log_outreach`, `start_run/finish_run`.
Keep all SQL/identity-key logic in this one file so the rest of the pipeline
stays storage-agnostic.

---

## 7. Config / secrets

Add to `.streamlit/secrets.toml` (and env): `SUPABASE_URL`, `SUPABASE_KEY`
(service-role key for the backend; **never** ship it to a browser client).
`db.py` reads them via the existing `load_secrets()` pattern. Add
`supabase>=2.0` to `requirements.txt`.

RLS: enable Row Level Security before any hosted/multi-client use; for the
single-client backend MVP the service-role key bypasses RLS, which is fine
server-side only.

---

## 8. Rollout plan (incremental, low-risk)

1. **Schema + `db.py`** behind `--db`, default off. Pipeline behaviour unchanged.
2. **Caching first** (biggest, safest win) — wrap enrichment; measure $ saved.
3. **Entity persistence** — upsert companies/contacts each run.
4. **Outreach dedup** — log on export, then filter on subsequent runs.
5. **Viewer reads DB** — optional, once data is flowing.
6. Later: auth + hosted UI (the separate P1) reuses this schema directly.

Effort estimate: steps 1–4 are ~1 focused day. The schema is the commitment;
everything else is additive and reversible (drop the `--db` flag → old path).

---

## 9. Open questions for you

- **Retention / TTL** — how stale is "too stale" for cached company research?
  (Suggest 45 days; contacts 90.)
- **One DB across clients, or one per client?** Schema supports multi-client via
  the `client` column today; per-client DBs only if a client demands isolation.
- **Source of truth for "contacted"** — does Oangle send from their own tool
  (then we only know `exported`), or do we want a webhook to mark `sent`/`bounced`?
