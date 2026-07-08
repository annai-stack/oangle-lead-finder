"""
Batch execution for the AI Lead Insights enrichment.

The Messages Batches API processes requests asynchronously at 50% of standard
token price — ideal for enriching hundreds of leads overnight. Each request is
processed ONCE, so a `pause_turn` (web-search loop hitting its cap) cannot be
continued mid-batch; the `max_uses` caps in insights_enrich keep requests to a
single turn, and any straggler that still pauses is logged (its JSON will be
empty → fields fall back to defaults).

Flow (three batches — contact enrichment now grounds on the company research,
so company must finish first; polish depends on draft results):
  Batch 1: all company enrichment (web search).
  Batch 2: all contact enrichment (no search; company research merged in).
  Batch 3: premium polish for high heat-rank leads only.
"""

import time

import cost_model
import insights_enrich as ie


def submit_and_wait(client, requests_list: list[dict], label: str,
                    poll_interval: int = 20) -> dict:
    """Submit a batch, poll to completion, return {custom_id: message}."""
    if not requests_list:
        return {}
    batch = client.messages.batches.create(requests=requests_list)
    print(f"      [{label}] submitted {len(requests_list)} requests · id={batch.id}")
    while True:
        b = client.messages.batches.retrieve(batch.id)
        if b.processing_status == "ended":
            break
        rc = b.request_counts
        print(f"      [{label}] {b.processing_status} · "
              f"processing={rc.processing} succeeded={rc.succeeded} errored={rc.errored}")
        time.sleep(poll_interval)

    results, paused, errored = {}, 0, 0
    for r in client.messages.batches.results(batch.id):
        if r.result.type == "succeeded":
            msg = r.result.message
            if getattr(msg, "stop_reason", None) == "pause_turn":
                paused += 1
            results[r.custom_id] = msg
        else:
            errored += 1
            print(f"      [{label}] {r.custom_id} → {r.result.type}")
    if paused:
        print(f"      [{label}] WARNING: {paused} request(s) hit pause_turn "
              f"(incomplete — lower max_uses or re-run those sync)")
    if errored:
        print(f"      [{label}] {errored} request(s) errored")
    return results


def _text(results: dict, cid: str) -> str:
    msg = results.get(cid)
    return ie.extract_text(msg) if msg is not None else ""


def enrich_batch(client, profile: dict, pairs: list[tuple],
                 draft_model: str, premium_model: str, premium_ranks: set) -> tuple[list, dict]:
    """Run the full enrichment via batches. Returns (records, cost_stages)."""
    # Unique companies (first occurrence keeps the firmographics/tech merged in stage 2)
    companies: dict[str, dict] = {}
    for co, _ in pairs:
        companies.setdefault(co.get("company_name", "") or co.get("website", ""), co)
    ckeys = list(companies)

    # --- Batch 1: company enrichment (web search) ---
    creqs = [{"custom_id": f"co_{i}",
              "params": ie.company_batch_params(profile, companies[ck], draft_model)}
             for i, ck in enumerate(ckeys)]
    res_co = submit_and_wait(client, creqs, label="company")

    company_enrich, co_usage = {}, cost_model.blank_usage()
    for i, ck in enumerate(ckeys):
        msg = res_co.get(f"co_{i}")
        if msg is not None:
            cost_model.add_usage(co_usage, msg.usage)
        company_enrich[ck] = ie._finalize_company(
            ie._parse_json_object(_text(res_co, f"co_{i}"), f"company:{ck}"))

    # --- Batch 2: contact enrichment (no search; company research merged in) ---
    treqs = []
    for i, (co, contact) in enumerate(pairs):
        ck = co.get("company_name", "") or co.get("website", "")
        merged_co = {**co, **company_enrich.get(ck, {})}
        treqs.append({"custom_id": f"ct_{i}",
                      "params": ie.contact_batch_params(profile, merged_co, contact, draft_model)})
    res_ct = submit_and_wait(client, treqs, label="contact")

    drafts, contact_usage = [], cost_model.blank_usage()
    for i, (co, contact) in enumerate(pairs):
        msg = res_ct.get(f"ct_{i}")
        if msg is not None:
            cost_model.add_usage(contact_usage, msg.usage)
        name = contact.get("contact_name") or contact.get("first_name", "?")
        drafts.append(ie._finalize_contact(
            ie._parse_json_object(_text(res_ct, f"ct_{i}"), f"contact:{name}"), profile, contact))

    # --- Batch 3: premium polish for top heat-rank leads ---
    polish_idx = [i for i, d in enumerate(drafts) if d.get("heat_rank") in premium_ranks]
    polish_usage = cost_model.blank_usage()
    if polish_idx:
        preqs = []
        for i in polish_idx:
            co, contact = pairs[i]
            ck = co.get("company_name", "") or co.get("website", "")
            merged_co = {**co, **company_enrich.get(ck, {})}
            preqs.append({"custom_id": f"pl_{i}",
                          "params": ie.polish_batch_params(profile, merged_co, contact,
                                                           drafts[i], premium_model)})
        res2 = submit_and_wait(client, preqs, label="polish")
        for i in polish_idx:
            msg = res2.get(f"pl_{i}")
            if msg is not None:
                cost_model.add_usage(polish_usage, msg.usage)
            drafts[i] = ie._finalize_polish(
                ie._parse_json_object(_text(res2, f"pl_{i}"), "polish"), drafts[i])

    # --- Assemble records ---
    records = []
    for (co, contact), draft in zip(pairs, drafts):
        ck = co.get("company_name", "") or co.get("website", "")
        records.append(ie.build_record(profile, co, contact, company_enrich.get(ck, {}), draft))

    stages = {
        "company enrich": (co_usage, draft_model),
        "contact enrich": (contact_usage, draft_model),
        "premium polish": (polish_usage, premium_model),
    }
    return records, stages
