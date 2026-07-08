# Oangle Lead Finder — Requirements Coverage Analysis

**Source of requirements:** Fireflies transcript "Yi Qi x Arvin Follow up on (1) Oangle / ST products" — call dated **21 May 2026** (`~/Downloads/arvin x yi qi.pdf`).
**Analysed against:** `~/akin-ai/lead-finder/` (code) and `oangle-lead-finder-deck.html` (the deck, also deployed at `oangle-deck/index.html`).
**Analysis date:** 18 Jun 2026.

> Note: the PDF is a *meeting transcript*, not a formal spec. Requirements below are extracted from what Arvin committed to build and what Yi Qi said Oangle needs.

---

## Part 1 — Were Oangle's requirements built into the Lead Finder?

| # | Requirement (from transcript) | Built? | Where |
|---|---|---|---|
| R1 | Formalise lead gen into a system/agent (replace the one in-house guy cold-calling/emailing) | ✅ Yes | Whole pipeline (`run_insights.py`) |
| R2 | Multi-stage prompt system — "3–4 stages of instructions" to long-list prospects by criteria | ✅ Yes (exceeded) | 5-stage pipeline: discover → contacts → enrich → verify → deliver |
| R3 | Produce a list of **~100 leads** the sales guy can follow up | ⚠️ Capable, not default | Scales via `--max-companies`/`--contacts-per-company`, but defaults to ~10 companies. 100 not demonstrated |
| R4 | Take **top 20–30**, research each, produce a **sales pitch** + what the rep needs to follow up | ✅ Yes | Heat rank 1A→4 prioritisation; "Personalised Sales Paragraph", "How to Target", "CVP vs Lead's Profile" columns; premium (Opus) polish on 1A/1B |
| R5 | Solve **"too many options"** pain — research which Oangle products fit each lead | ✅ Yes | `Suggested Products` per lead (POS/SOK/ORB/DMB). Directly addresses Yi Qi's complaint |
| R6 | **Tweakable signals** — AKIN can adjust what to look for | ✅ Yes | `client_config.py` (ICP, titles, rubric, segments) — config, not code |
| R7 | ICP: chains, fast food, set menus, kiosk/no-seating (food trucks, bubble tea), QSR, cloud kitchens; **avoid** Chinese-style/custom/fine-dining/omakase | ✅ Yes (verbatim) | `TARGET_SEGMENTS`, ICP string, and hard disqualifiers in `SYSTEM_PROMPT` |
| R8 | **Grant angle** — AI add-ons to qualify for SG govt grants / push up fees | ✅ Flagged | `grant_eligible` column + `value_prop`. (Flags eligibility; building the actual AI add-on is separate product work) |
| R9 | **Warm-intro channel** — kitchen-equipment partner brands (Subway, Guzman, KFC) | ✅ Yes | `WARM_INTRO_BRANDS` + `warm_intro` column + scoring boost |
| R10 | **Pluggable clusters** — restaurant + kitchen first, then plug in the rest | ⚠️ **Partial — biggest gap** | See below |
| R11 | MVP first, Oangle makes it production-ready, in a repo | ✅ Yes | Git repo MVP; deck "Status / What's next" covers handoff/hosting |
| R12 | Match their past play: **customised brochure/deck per brand** + tell them the package | ⚠️ Partial | Personalised paragraph + suggested products ≈ "the package", but no per-brand brochure/deck is generated |
| R13 | Non-reply follow-up (LinkedIn / non-digital) | ➖ Out of scope | Arvin explicitly deferred ("consult you after") |

### The key gap — R10: other product clusters
The transcript is explicit that Oangle has **multiple product clusters** and the MVP should let the rest "plug in":
- **Restaurant + Kitchen tech** — ✅ built (this is Phase 1). The call frames these as the *same target audience* ("restaurant and kitchen … the same kind of target audience"; "I can start with the restaurant and kitchen first"), so the F&B profile covers both.
- **Maintenance tech** (service & maintenance, soft/hardware) — ❌ not built. Yi Qi himself said it's "not fleshed out".
- **Event** (event organisers, booking system, freelancer-on-the-day play) — ❌ not built.
- **Building / facility management** (CST cold-call, MCST, outsourced building mgmt) — ❌ not built.

The architecture *is* genuinely pluggable (a new cluster/client = one `client_config.py` dict entry), so the foundation Arvin promised exists — but only the Oangle F&B profile is implemented. This is consistent with the agreed "Phase 1 MVP" scope, **but it should be stated as the explicit roadmap**, because the clusters were a core part of what Arvin described on the call.

---

## Part 2 — Is everything about the Lead Finder covered in the deck?

**Mostly yes.** The deck faithfully represents the core system. Confirmed present: 5-stage pipeline, all data sources (Claude/web, Hunter, Apollo, BuiltWith, Supabase), 23-column output by source, heat-rank rubric, verification layer (email/source/claim → Data Confidence + Review tab), live Streamlit console, persistence/dedup/cache, the AKIN↔Oangle working loop, status, and what's-next.

### Deck-accuracy check (overclaims) — verified, no false "Live" claims
I checked the deck in both directions: not just what it omits, but whether anything it labels **Live** is actually aspirational. The one at-risk claim was *"Live — database persistence: lead history, outreach dedup, research caching"* (the README calls Supabase off-by-default/optional). **Verified live on 18 Jun 2026:** Supabase is configured (`secrets.toml`), the `supabase` package is installed, `db.get_store` is wired through `run_insights.py`, and a read-only connection shows real data — `runs`=5, `enrichment_cache`=2, `outreach`=12 rows. The "Live" status is accurate. Pipeline stages and the personalised-paragraph/premium-polish steps were also confirmed in code (`run_insights.py` Stages 1–3 + verify, `insights_enrich.py`), not just the README.

### What the Lead Finder does but the deck does NOT show

| Feature in build | In deck? | Why it matters |
|---|---|---|
| **Cost model / economics** (`cost_model.py`): ~$0.07–0.22 per lead, Sonnet-draft + Opus-polish lever, **Batch API 50% cheaper** | ❌ No cost/pricing slide at all | For a pitch/handover deck, Oangle will want running cost. This is the most notable omission |
| **Batch mode** (`--batch`, async, 50% cheaper) | ❌ Not mentioned | A real efficiency feature |
| **Scale / throughput** (the "~100 leads" the call promised) | ❌ Not quantified | Deck shows ~10–12 leads in the demo; no statement of how big a run can be |
| **Other product clusters** as a roadmap (kitchen/maintenance/event/building) | ❌ Not mentioned | "What's next" lists hosting, compliance, CRM, "more clients" — but not Oangle's *own* other clusters from the call |

### Minor consistency note
- Deck says "**23+** reference columns, plus 3 verification columns"; README describes 23 columns total with verification fields appended. Harmonise the count wording.

---

## Recommendation (priority order)
1. ✅ **DONE (18 Jun 2026)** — **Roadmap-clusters slide added** (Restaurant+Kitchen LIVE → Maintenance → Event → Building/Facilities, "one config entry each"), closing the most visible promise from the call.
2. **Add a cost/economics slide** (~$0.07/lead batched; cost levers) — material for a handover/decision deck. *(Still open.)*
3. ✅ **DONE (18 Jun 2026)** — **Run scale stated** ("100+ leads per run" stat on the Output slide) so the deck matches the "100 leads" Arvin promised.
4. (Optional) Decide whether a **per-brand brochure/deck generator** (R12) is in scope — currently the output is a sheet + paragraph, not a branded deck.
5. ✅ **DONE** — column wording clarified ("23+ reference + 3 verification & account-context columns").

> Deck-edit mechanics (18 Jun): adding a slide + stat shifted the editor's positional keys, so `DECK.id` was bumped to `oangle-lead-finder-v2` — the deck now renders from the HTML (source of truth) and starts a clean version history. Anna's earlier wording change ("reusable for any client" removed from the title) was baked into the HTML so it wasn't lost. Both `oangle-lead-finder-deck.html` and the deployed `oangle-deck/index.html` are updated and identical.
