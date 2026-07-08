-- AI Lead Insights — Supabase / Postgres schema
-- Run once in the Supabase SQL editor (or `psql`) to provision persistence.
-- Everything the pipeline writes degrades gracefully: if these tables are
-- missing or unreachable, db.py falls back to a no-op store and the file-based
-- pipeline runs unchanged.

-- A company, deduped by domain (fallback: normalised brand name).
create table if not exists companies (
  id            uuid primary key default gen_random_uuid(),
  client        text not null,                  -- 'oangle' (matches client_config key)
  domain        text,                            -- canonical, from apollo_client.extract_domain
  name_norm     text not null,                   -- lower(trim(brand_name)); dedup key when domain missing
  company_name  text,
  brand_name    text,
  segment       text,
  num_outlets_sg int,
  fit_score     int,
  fit_reason    text,
  source_url    text,
  data          jsonb default '{}',
  created_at    timestamptz default now(),
  updated_at    timestamptz default now()
);
-- Domain dedup (partial: only where a domain exists). NULL domains dedup by name.
create unique index if not exists companies_client_domain_uidx
  on companies (client, domain) where domain is not null;
create unique index if not exists companies_client_name_uidx
  on companies (client, name_norm);

-- A decision-maker, deduped by email (fallback: insert when no email).
create table if not exists contacts (
  id            uuid primary key default gen_random_uuid(),
  company_id    uuid references companies(id) on delete cascade,
  client        text not null,
  email         text,
  email_norm    text,                            -- lower(trim(email)); primary dedup key
  linkedin_url  text,
  first_name    text,
  last_name     text,
  job_title     text,
  seniority     text,
  source        text,                            -- 'Apollo' | 'Hunter'
  run_id        uuid,                             -- the runs.id that last generated/refreshed this lead
  email_result      text,                        -- deliverable/risky/undeliverable (verify.py)
  data_confidence   text,                        -- high/med/low (objective, not self-reported)
  verification_notes text,
  data          jsonb default '{}',              -- the full enriched export record
  created_at    timestamptz default now(),
  updated_at    timestamptz default now()
);
create unique index if not exists contacts_client_email_uidx
  on contacts (client, email_norm) where email_norm is not null;
create index if not exists contacts_run_idx on contacts (run_id);

-- Cache of expensive enrichment + firmographics, keyed by entity + prompt
-- version. This is the cost-saver: a rerun reuses these instead of re-calling
-- Claude / Apollo. kind ∈ {company, contact, polish, org}.
create table if not exists enrichment_cache (
  id            uuid primary key default gen_random_uuid(),
  client        text not null,
  kind          text not null,
  entity_key    text not null,                   -- domain (company/org) or email_norm (contact/polish)
  prompt_version text not null default 'v1',     -- bump in insights_enrich to invalidate
  payload       jsonb not null,
  created_at    timestamptz default now(),
  unique (client, kind, entity_key, prompt_version)
);
create index if not exists enrichment_cache_created_idx on enrichment_cache (created_at);

-- One row per pipeline invocation (provenance + cost-over-time).
create table if not exists runs (
  id            uuid primary key default gen_random_uuid(),
  client        text not null,
  args          jsonb,
  n_companies   int,
  n_contacts    int,
  cost_usd      numeric(10,4),
  started_at    timestamptz default now(),
  finished_at   timestamptz
);

-- Dedup-on-outreach log: who was exported/sent, so future runs can skip them.
create table if not exists outreach (
  id            uuid primary key default gen_random_uuid(),
  contact_id    uuid references contacts(id) on delete cascade,
  client        text not null,
  run_id        uuid references runs(id),
  status        text not null default 'exported', -- exported | sent | replied | bounced | suppressed
  channel       text default 'email',
  created_at    timestamptz default now()
);
create index if not exists outreach_contact_status_idx on outreach (contact_id, status);

-- ---------------------------------------------------------------------------
-- In-browser deck editing — append-only version history for editable HTML decks
-- (see oangle-lead-finder-deck.html). Every Save inserts a new row; "current"
-- content is the most recent row; "revert" inserts a copy of an older row.
-- ---------------------------------------------------------------------------
create table if not exists deck_versions (
  id          uuid primary key default gen_random_uuid(),
  deck        text not null default 'oangle-lead-finder',
  content     jsonb not null,                  -- { "k0": "<html>", "k1": "...", ... }
  author      text,
  note        text,
  created_at  timestamptz default now()
);
create index if not exists deck_versions_deck_created_idx
  on deck_versions (deck, created_at desc);

-- The deck is edited directly from the browser with the ANON (public) key, so
-- it needs Row Level Security policies. This grants read + append to anyone
-- with the anon key. Append-only + version history means any unwanted edit is
-- fully reversible. To lock editing down to named users later, replace the
-- insert policy with one requiring Supabase Auth (auth.role() = 'authenticated').
alter table deck_versions enable row level security;

drop policy if exists deck_versions_read on deck_versions;
create policy deck_versions_read on deck_versions
  for select using (true);

drop policy if exists deck_versions_append on deck_versions;
create policy deck_versions_append on deck_versions
  for insert with check (true);

-- ---------------------------------------------------------------------------
-- Lock the LEAD tables to the SECRET (service) key only.
-- The deck is edited in the browser with the PUBLISHABLE key, which is public.
-- Enabling RLS with NO policy on these tables denies the publishable key, so a
-- deck viewer can never read your leads. The SECRET key used by the pipeline
-- bypasses RLS, so the lead pipeline is unaffected. (deck_versions intentionally
-- keeps its read+append policies above so the browser can edit slides.)
-- ---------------------------------------------------------------------------
alter table companies        enable row level security;
alter table contacts         enable row level security;
alter table enrichment_cache enable row level security;
alter table runs             enable row level security;
alter table outreach         enable row level security;
