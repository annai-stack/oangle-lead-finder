"""
Cost model for the AI Lead Insights pipeline.

Encodes Anthropic token pricing + the web-search tool surcharge and turns a
response `usage` object into a dollar figure. Used to print a per-run, per-stage
token-fee breakdown. Pricing verified against platform.claude.com (2026-06).
"""

# $ per 1M tokens (input, output). cache_read ≈ 0.1× input; cache_write ≈ 1.25× input.
PRICING = {
    "claude-opus-4-8":   {"input": 5.0,  "output": 25.0},
    "claude-sonnet-4-6": {"input": 3.0,  "output": 15.0},
    "claude-haiku-4-5":  {"input": 1.0,  "output": 5.0},
}
CACHE_READ_MULT  = 0.1
CACHE_WRITE_MULT = 1.25
WEB_SEARCH_PER_SEARCH = 10.0 / 1000  # $0.01 per search ($10 / 1,000)


def blank_usage() -> dict:
    return {"input_tokens": 0, "output_tokens": 0,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
            "web_search_requests": 0}


def add_usage(acc: dict, resp_usage) -> dict:
    """Accumulate an Anthropic response.usage object into a running dict."""
    acc["input_tokens"]  += getattr(resp_usage, "input_tokens", 0) or 0
    acc["output_tokens"] += getattr(resp_usage, "output_tokens", 0) or 0
    acc["cache_read_input_tokens"]     += getattr(resp_usage, "cache_read_input_tokens", 0) or 0
    acc["cache_creation_input_tokens"] += getattr(resp_usage, "cache_creation_input_tokens", 0) or 0
    stu = getattr(resp_usage, "server_tool_use", None)
    if stu is not None:
        acc["web_search_requests"] += getattr(stu, "web_search_requests", 0) or 0
    return acc


def cost(usage: dict, model: str = "claude-opus-4-8", batch: bool = False) -> dict:
    """Return a {tokens..., token_cost, search_cost, total} breakdown in USD.

    batch=True applies the Batches API 50% token discount (web-search fees are
    unchanged in batch).
    """
    p = PRICING.get(model, PRICING["claude-opus-4-8"])
    token_mult = 0.5 if batch else 1.0
    token_cost = token_mult * (
        usage["input_tokens"] * p["input"]
        + usage["output_tokens"] * p["output"]
        + usage["cache_read_input_tokens"] * p["input"] * CACHE_READ_MULT
        + usage["cache_creation_input_tokens"] * p["input"] * CACHE_WRITE_MULT
    ) / 1_000_000
    search_cost = usage["web_search_requests"] * WEB_SEARCH_PER_SEARCH
    return {
        **usage,
        "token_cost": round(token_cost, 4),
        "search_cost": round(search_cost, 4),
        "total": round(token_cost + search_cost, 4),
    }


def format_breakdown(stages: dict, model: str = "claude-opus-4-8") -> str:
    """stages: {stage_name: usage_dict}. Returns a printable per-stage table."""
    lines = [f"\n=== Token-Fee Breakdown ({model}) ===",
             f"{'Stage':<22}{'in':>10}{'out':>9}{'cache_rd':>10}{'srch':>6}{'$ total':>10}"]
    grand = blank_usage()
    total_dollars = 0.0
    for name, u in stages.items():
        c = cost(u, model)
        for k in grand:
            grand[k] += u.get(k, 0)
        total_dollars += c["total"]
        lines.append(f"{name:<22}{u['input_tokens']:>10,}{u['output_tokens']:>9,}"
                     f"{u['cache_read_input_tokens']:>10,}{u['web_search_requests']:>6}"
                     f"{c['total']:>10.4f}")
    lines.append("-" * 67)
    lines.append(f"{'TOTAL':<22}{grand['input_tokens']:>10,}{grand['output_tokens']:>9,}"
                 f"{grand['cache_read_input_tokens']:>10,}{grand['web_search_requests']:>6}"
                 f"{total_dollars:>10.4f}")
    return "\n".join(lines)
