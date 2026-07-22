# Lead Finder — demo guide

How to demo the Lead Finder to a prospect without it falling over.
Verified against the live build on **22 Jul 2026**.

---

## The one rule

**Demo from pre-generated runs. Never click Run in front of a prospect.**

A live run takes **~4.5 minutes per lead** (measured: 2 leads = 8 min 58 s). Ten
leads is roughly 45 minutes of silent progress bar. There is no way to make that
look good on a call.

This is not a limitation you need to hide — see *Handling the speed question*.

---

## Before the call (15 minutes)

1. **Start both apps** (two terminal tabs, from `lead-finder/`):

   ```bash
   .venv/bin/streamlit run app.py                       # → localhost:8501  main UI
   .venv/bin/streamlit run insights_app.py --server.port 8502   # → localhost:8502  contact insights
   ```

2. **Load both tabs and click through once**, so Streamlit is warm and nothing
   renders for the first time in front of the client.

3. **In the 8502 tab, switch ON "Show previously generated leads"** in the
   sidebar. Confirm it reads *10 leads · 2 Hot (1A) · 5 companies · 10 with email*.

4. **In the 8501 tab, open Previous Runs** and select the **most recent**
   `leads_fnb_*.csv` (the backfilled one). Confirm the top metrics show
   *26 leads · 1 warm intro · 24 grant eligible*. If warm intros shows `—`,
   you have the old June CSV selected — switch files.

5. **Close every other tab.** The sidebar no longer shows API-key status, but
   your browser might show something you would rather it did not.

---

## The demo, in order (~10 minutes)

### 1. Frame the problem (30 seconds, no screen)

> "Today one person picks who to call, researches them by hand, and writes each
> email. This finds the accounts, ranks them, finds the decision-maker, and
> drafts the approach — so the rep spends their time selling, not researching."

### 2. The account list — *8501 → Previous Runs*

Show the table. Talk to what is on screen:

- **26 F&B chains in Singapore**, scored 1–5 against Oangle's ICP
- **The "Why" column** — every score has a stated reason ("~77 high-volume
  outlets with a standardised fried-chicken menu")
- **Outlet counts** — 130 McDonald's, 85 LiHO, 77 KFC
- **KFC sits at the top because it is a warm intro** through the kitchen-equipment
  partner, ahead of higher-scoring cold accounts

Use the **filters** live — segment, score, "Warm only" — to make the point that
the rep works a prioritised list, not a spreadsheet dump.

### 3. The contact layer — *switch to 8502*

This is where it lands. Show the ten enriched contacts:

- Real named decision-makers: **Yohannan Johnson, General Manager, KFC
  Singapore**; **Craig Hapa, Director of Operations, Coffee Bean**
- **Heat ranks** 1A → 4, so the rep knows who to call first
- **All ten have a work email and a LinkedIn URL**

Then open one **lead card** and read the AI columns aloud — this is the part
that sells:

- **Suggested Products** — POS, SOK, DMB, ORB (which of the four to pitch)
- **CVP against the lead's profile** — why Oangle fits *this* company
- **Personalised sales paragraph** — a ready first-touch message

> "This directly answers the 'too many options' problem — the rep is told which
> product to lead with and why, per account."

### 4. Close — the download

Click **Download full AI Lead Insights (.xlsx)**. Land the point:

> "That is the deliverable. It drops into whatever the sales team already uses."

---

## Handling the speed question

If asked "how fast is this?" — be straight:

> "It runs as a batch, not in real time. Deep research per lead is the reason
> the output is good. You queue a run and the list is waiting the next morning."

**Do not** claim a live 10-minute run for 100 leads. The scale is real (set by
one number in the config), the speed is not.

---

## Questions you should expect

| Question | Honest answer |
|---|---|
| "Can we run it on our own list?" | Yes — feed a company list, or it discovers accounts itself. |
| "How much per lead?" | About **$0.20** in AI cost, plus contact-data credits. Batch mode roughly halves it. |
| "Can we change what it looks for?" | Yes — ICP, segments, target titles and scoring rubric are config, not code. One entry per client. |
| "Other parts of our business?" | Restaurant + kitchen is live. Maintenance, Event and Building are the same architecture — **not yet built**. Say roadmap, not ready. |
| "Where does it run?" | Today on a laptop. Hosting is part of handover. |
| "Where does the contact data come from?" | Hunter.io primary, Apollo for firmographics. Both are the client's own accounts in production. |

---

## Do not do these

- ❌ Click **Run / New Run** — 45+ minutes of nothing
- ❌ Demo the **Generic Engine** with a made-up profile — it runs live
- ❌ Show a **vertical that is not built** (Maintenance / Event / Building)
- ❌ Promise **100 leads in 10 minutes**
- ❌ Show the **June CSV** — warm intro and grant columns read `—`

---

## Known rough edges

- **Contact data is thin on a fresh run.** The pre-baked set has emails for all
  ten; a new run on the current free Hunter tier will not match that. Upgrade
  Hunter before generating anything new for a client.
- **Only one warm intro** in the sample. Subway and Guzman y Gomez are on the
  partner list but were never discovered in the June run. A fresh discovery pass
  would likely surface all three and make the warm-intro story much stronger.
- **Two apps, two ports.** Account list on 8501, contact insights on 8502. Have
  both open before the call; do not start one mid-demo.

---

## If something breaks

- **Page hangs / spinner stuck** — do not wait. Say "let me show you the output
  it produces" and open the downloaded `.xlsx` from a previous run.
- **App dies** — the `.xlsx` files in the repo root are a complete offline
  fallback. Keep one open in Excel in a background window before you start.
