# In-browser deck editing — AKIN ↔ Oangle workflow

The pitch deck (`oangle-lead-finder-deck.html`) can be edited in the browser and
saved to Supabase, with full version history and one-click revert — no terminal,
no code. This is the agency↔client workflow built around it.

---

## Roles

| Who | Role | How they open it | What they can do |
|-----|------|------------------|------------------|
| **AKIN** (agency) | **Editors** | `…/deck.html?edit` | Click **Edit** → enter passphrase → change any text → **Save**. View **History** / **Revert**. **Export Excel**. |
| **Oangle** (client) | **Viewers** | `…/deck.html` (no `?edit`) | See the latest AKIN-approved content. No Edit button appears. Cannot change anything. |

Both links always load the **newest saved version** from Supabase. Every Save is
an immutable, revertable version.

---

## One-time setup

1. **Create the Supabase project** and run `supabase_schema.sql` (provisions the
   `deck_versions` table alongside the lead-finder tables — *same project*, no
   separate one needed).
2. In `oangle-lead-finder-deck.html`, fill the `DECK` config block near the
   bottom:
   ```js
   const DECK = {
     id: 'oangle-lead-finder',
     supabaseUrl:     'https://xxxx.supabase.co',   // Project URL
     supabaseAnonKey: 'eyJ…',                        // ANON / public key (NOT service_role)
     editPassphrase:  'pick-a-passphrase',           // give this to the AKIN team only
   };
   ```
   Use the **anon** key here (it's meant to be public, protected by the table's
   RLS policies) — never the service-role key.
3. Deploy the file (e.g. Vercel) so the team has a shared URL, or open it locally.
   Offline / unconfigured, it still presents normally with no Edit button.

---

## Day-to-day

- **AKIN edits:** open `<url>?edit` → **Edit** → passphrase → click any text and
  type → **Save** (you'll be asked for your name + an optional note). Done.
- **Send to Oangle:** share the plain `<url>` (no `?edit`). They always see the
  latest saved content.
- **Mistake?** **History** → **Preview** any past version → **Revert** (revert
  saves a new version with the old content, so nothing is ever lost).
- **Need a spreadsheet snapshot?** **Export Excel** dumps the current slide text.

---

## Running a second deck (e.g. an AKIN-branded deck)

You don't need another Supabase project. To add a separate deck:

1. Copy `oangle-lead-finder-deck.html` to e.g. `akin-deck.html`.
2. Change **one line** — the `id`:
   ```js
   id: 'akin-overview',   // distinct id → its own version history
   ```
3. Keep the same `supabaseUrl` / `supabaseAnonKey`.

Both decks store versions in the same `deck_versions` table, separated by the
`deck` column — so AKIN's deck history and Oangle's deck history never mix.

---

## Security posture (honest)

- The **anon key is public by design**; the `deck_versions` RLS policies allow
  read + append. The passphrase is a **UI deterrent**, not hard security — anyone
  who has both the `?edit` link and the passphrase can edit.
- Because history is **append-only + revertable**, any unwanted edit is fully
  reversible — worst case is an extra version you revert past.
- To lock editing to named users later, replace the `deck_versions` insert policy
  with one requiring Supabase Auth (`auth.role() = 'authenticated'`) and add a
  login step. Until then, treat the `?edit` link + passphrase as the gate.
