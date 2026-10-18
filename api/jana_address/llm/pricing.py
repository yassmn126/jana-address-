"""Claude API prices, USD per million tokens.

Source: https://platform.claude.com/docs/en/about-claude/pricing (checked 2026-10-18). Update this table when the page
changes; `cost_usd` is what /api/v1/ai/status, the eval CLI and docs/AI_MODE.md use.

Columns: base input, 5-minute cache write, 1-hour cache write, cache read (hit), output.
"""
from __future__ import annotations

PRICES: dict[str, tuple[float, float, float, float, float]] = {
    "claude-fable-5-1": (10.0, 12.5, 20.0, 0.25, 50.0),
    "claude-mythos-5-1": (10.0, 12.5, 20.0, 0.25, 50.0),
    "claude-opus-5-5": (4.0, 5.0, 8.0, 0.20, 20.0),
    "claude-sonnet-5-5": (2.0, 2.5, 4.0, 0.10, 10.0),
    "claude-haiku-5-5": (0.10, 0.125, 0.20, 0.01, 0.50),       # prompts up to 100k tokens (ours: ~12k)
    "claude-opus-5": (5.0, 6.25, 10.0, 0.50, 25.0),
    "claude-sonnet-5": (2.0, 2.5, 4.0, 0.20, 10.0),
    "claude-opus-4-8": (5.0, 6.25, 10.0, 0.50, 25.0),
    "claude-opus-4-7": (5.0, 6.25, 10.0, 0.50, 25.0),
    "claude-opus-4-6": (5.0, 6.25, 10.0, 0.50, 25.0),
    "claude-opus-4-5": (5.0, 6.25, 10.0, 0.50, 25.0),
    "claude-sonnet-4-6": (3.0, 3.75, 6.0, 0.30, 15.0),
    "claude-sonnet-4-5": (3.0, 3.75, 6.0, 0.30, 15.0),
    "claude-haiku-4-5": (1.0, 1.25, 2.0, 0.10, 5.0),
}
HAIKU_5_5_LONG = (0.50, 0.625, 1.0, 0.05, 2.50)                # Haiku 5.5 prompts over 100k tokens
BATCH_DISCOUNT = 0.5                                             # Message Batches API: 50 % off input and output


def price_row(model: str) -> tuple[float, float, float, float, float] | None:
    """Longest-prefix match, so dated snapshots («claude-haiku-5-5-20261007») resolve too."""
    best = None
    for key in PRICES:
        if model.startswith(key) and (best is None or len(key) > len(best)):
            best = key
    return PRICES[best] if best else None


def cost_usd(model: str, input_tokens: int = 0, output_tokens: int = 0, cache_write_tokens: int = 0,
             cache_read_tokens: int = 0, batch: bool = False, cache_ttl_1h: bool = False,
             aggregate: bool = False) -> float | None:
    """Cost of one request, or of usage summed over many requests (aggregate=True: Haiku 5.5's >100k-prompt tier is
    decided per request, so it is not applied to sums). None for an unknown model (e.g. a local vLLM model)."""
    row = price_row(model)
    if row is None:
        return None
    prompt_tokens = input_tokens + cache_write_tokens + cache_read_tokens
    if model.startswith("claude-haiku-5-5") and prompt_tokens > 100_000 and not aggregate:
        row = HAIKU_5_5_LONG
    base_in, w5, w1h, read, out = row
    usd = (input_tokens * base_in + cache_write_tokens * (w1h if cache_ttl_1h else w5)
           + cache_read_tokens * read + output_tokens * out) / 1e6
    if batch:
        usd *= BATCH_DISCOUNT
    return usd
