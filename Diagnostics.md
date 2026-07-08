# AKIN Brand Diagnostic — product brief (handover)

**For:** the teammate scoping and building this tool.
**What you are building:** a fast, public-facing brand diagnostic that AKIN uses as a teaser at talks and through partners. It gives a user a quick brand score and a competitor read, presents the result like a personality-test profile, and routes them toward what AKIN can do for them. Build the lightweight "instant" version described here. You do not need access to AKIN's internal products to build it; this brief gives you the outcomes the result should point to.

## Why it exists

It opens a conversation. A founder or marketer gets something genuinely useful in about a minute, sees where their brand is strong and weak, and is shown how AKIN can close the gap. The result becomes the qualifying brief for a paid (often grant-subsidised) engagement, so the tool is a pipeline opener, not a revenue centre.

## Scope and audience

- **Scope:** the instant version only. About a minute end to end, cheap to run, works on stage and self-serve at scale. No long async pipeline.
- **Audience:** ready SMEs, startups, and line-of-business owners and managers. Their public footprint is thin, so the tool leans on what the user gives it (their URL, the competitors they name, their answers), not open-web discovery.

## How it works (flow)

1. User enters brand name, website URL (required), industry, primary market (default Singapore), and their role.
2. User lists **at least two competitors** (name or URL). This removes any discovery step the user could disagree with.
3. The tool crawls the user's URL and the competitor URLs.
4. User answers **6 to 8 short questions** generated from the crawl to fill gaps.
5. The tool scores the brand across the clusters below, builds a competitor read, and returns a shareable result with a pre-formed archetype and next steps.
6. The instant score is free; a deeper competitor read and downloadable report sit behind an email gate (the lead capture).

## Scoring clusters

Score each 0 to 100 from the crawled text (the user's URL, plus competitor URLs for the competitive cluster), refined by the answers.

1. **Believable narrative and proof** — is the story credible and evidenced (real proof, numbers, customer evidence) rather than vague adjectives. From their URL.
2. **Audience and market clarity** — do they show they know who they serve and the market context (segment-specific language, local relevance). From their URL, refined by a question on their audience.
3. **Proposition and product definition** — is what they sell clearly and distinctively defined; a quick read of what they actually do, taken from their own URL. Refined by a question on their claimed differentiator.
4. **Competitive strength** — against the competitors they named, is the proposition strong enough to stand out and consistent with what they claim. From comparing their URL to the competitor URLs.

**Optional fifth:** **Discoverability / demand** — how visible the brand is in search and AI answer engines. Add only if a light visibility signal is cheap to fetch.

**Alternative framing to consider:** the same substance ordered as a buying journey, Clarity, Credibility, Differentiation, Demand, which reads naturally to a founder.

**Overall score:** a composite 0 to 100 "brand strength" with AKIN-named bands (for example Emerging, Developing, Sharp, Market-leading) plus the archetype. Do not use any external tier or certification label.

## The 6 to 8 questions

Generated from the crawl, tuned to the business, to fill what the crawl cannot infer and to sharpen the scores: primary goal, who they believe their audience is, their claimed differentiator, biggest growth blocker, and budget or timeline. Keep them short; the audience is time-poor.

## Competitor read

Crawl the two-plus named competitors alongside the user's URL and place the brand against them on a simple map (a 2x2 or radar) scored on the same clusters, with one line on where it leads and where it trails. No open-web discovery.

## The result (personality-test profile)

A shareable page (plus a PDF, link, and social card for sharing at talks) with:
- A named **brand archetype** from a **pre-formed fixed set** (about 6 to 9), selected by the pattern of strong and weak clusters. Pre-forming keeps it instant and makes results comparable and shareable.
- A **radar** of the cluster scores and the overall band.
- The **competitor snapshot** against their named competitors.
- Per cluster, a strength and the one gap that matters.
- Each archetype carries **pre-written outputs**: suggested next steps, tools to investigate, and a strategy to explore with AKIN, matched to that score pattern. Write these once per archetype so the result is instant.

Tone: elevation, not a scolding. "You are here, this is the next step, here is how AKIN helps, much of it can be grant-funded."

## Routing into AKIN (what the result points to)

Map each weak cluster to an AKIN **AgencyOS outcome**, described below so you can build the routing without needing examples of the products themselves:

| Weak cluster | AgencyOS outcome to route to |
|---|---|
| Believable narrative and proof | **Brand genAI** and **creative genAI** |
| Audience and market clarity | **Market intelligence** |
| Proposition and product definition | **Brand genAI** (positioning) |
| Competitive strength | **Go-to-market** and **marketing and revenue acquisition** |
| Discoverability / demand (optional) | **Marketing and revenue acquisition** |

Plus a standing **funding callout**: "Up to 70 percent funded. Many Singapore companies can have an AKIN engagement supported by the CTC grant, which covers 50 to 70 percent of qualifying cost. We map your eligibility before we quote." One primary call to action: book a pilot, or unlock the full report.

## What AGENCYOS delivers (the outcomes the result routes to)

You do not need product demos to build this. Route users toward these outcomes:

- **Market intelligence** — a decision-ready read on a company's market, audience, and competitors, produced in about an hour rather than weeks.
- **Brand genAI** — AI-assisted brand strategy, narrative, and positioning that holds the brand's own voice.
- **Creative genAI** — on-brand creative and content produced at volume, with a human approval gate so quality and voice hold.
- **Go-to-market** — turning that intelligence into a clear go-to-market plan and motion.
- **Marketing and revenue acquisition** — account-based marketing and acquisition that identifies the right targets and produces outreach, so the revenue motion always knows who to reach.

The headline AgencyOS outcome to convey: a small team produces far more than a traditional one (roughly an order of magnitude more output with a fraction of the headcount), because AI carries the volume and people keep the judgement.

## Guardrails

- **Naming.** AgencyOS is the only product name to use publicly. Do not expose internal system or engine names, and do not reference any external training body or certification tier anywhere in the tool.
- **Voice (AKIN house style).** No em dashes; British English (organisation, optimise, behaviour); sentence-case headings; spell out one to nine and use numerals for 10 and above; no exclamation marks; no AI-writing tics (no filler lists of three, no "it is important to note").

## Cost and abuse guards

Cache by domain, rate-limit, and use a cheaper model for extraction and a stronger one for synthesis. Give the instant score free, then gate the full competitor read and downloadable report behind an email (the gate is both the cost cap and the lead capture). Target well under USD 0.50 per run.

## Delivery modes (one build, configurable)

- Self-serve web link (QR code at talks).
- On-stage live mode (run it on a volunteer's URL in under a minute).
- Partner white-label (partner logo, domain, and lead routing).

## Open questions for the team

- Confirm the four clusters above, or adopt the buying-journey framing (Clarity, Credibility, Differentiation, Demand), and whether to include the optional discoverability cluster.
- Confirm the free vs gated split (instant score free, competitor read and report gated).
- Working name and domain (candidates: "AI Brand Scan", "Brand Strength Scan"; for example scan.helloakin.com).
- The fixed archetype list and each archetype's pre-written next steps, tools, and strategy.
- Minimum competitor count (default two) and whether to accept names as well as URLs.

## Verification (once built)

Run a known SME URL plus two named competitors through the flow. Confirm the cluster scores and overall band render, the competitor snapshot returns the named competitors, the pre-formed archetype and its next steps match the score pattern, the funding callout and call to action appear, no internal or external-body names leak, the email gate fires before the full report, and per-run cost stays under target. For on-stage mode, complete on a volunteer's URL inside 60 seconds.
