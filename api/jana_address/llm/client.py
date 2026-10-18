"""Runtime AI client: batches of addresses → Claude (or a local OpenAI-compatible model) → validated JSON.

Request design (why each knob is set the way it is):
  * structured outputs — `output_config.format` = our JSON schema. The model can only emit parseable JSON with exactly
    our keys; no regex-scraping of free text, no "Sure! Here is…".
  * effort = "low" — the extraction is short; on Claude 5.x models effort is THE control of thinking depth (thinking
    tokens are billed as output). Raise it per request for hard sets (JANA_AI_EFFORT=medium|high).
  * no temperature — Claude Opus 4.7+, Sonnet 5.5 and Haiku 5.5 reject a non-default temperature/top_p/top_k with a 400
    (the SDK does not even accept the argument). It is still sent as 0 to older Claude models that take it
    (Haiku 4.5, Sonnet 4.5/4.6, Opus ≤ 4.6) and to a local vLLM model. Determinism comes from the schema, the
    rules + examples in the prompt, and the validators in policy.py — not from sampling.
  * prompt caching — explicit breakpoint on the system block: the ~12k-token prompt is written once per 5 minutes and
    read at 0.05–0.1x afterwards. (A top-level automatic breakpoint would land on the per-batch user message and
    re-write the whole prompt every call.)
  * batching — 20 addresses per request amortize the prompt and the per-request latency; requests run in parallel
    (JANA_AI_CONCURRENCY), and a batch that hits max_tokens is split in half and retried.
  * failure handling — SDK retries 429/5xx with backoff; after JANA_AI_MAX_FAILURES consecutive failures the circuit
    opens for JANA_AI_COOLDOWN_S and every record keeps its local answer (the service never blocks on the API).
  * privacy — phone numbers and e-mails are masked before sending (conventions ignore them anyway).

Credentials: ANTHROPIC_API_KEY (or `ant auth login`), never in the repo. Local model: JANA_AI_PROVIDER=openai-compatible,
OPENAI_BASE_URL=http://gpu-box:8000/v1 (vLLM), JANA_AI_MODEL=<served model name>.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .pricing import cost_usd
from .prompt import OUTPUT_SCHEMA, prompt_version, system_prompt, user_message

MODES = ("off", "foreign", "fallback", "always")

# Claude models that still accept sampling parameters (Opus 4.7+, Sonnet 5.x, Haiku 5.x and later reject them).
_SAMPLING_OK = ("claude-haiku-4-5", "claude-sonnet-4-5", "claude-sonnet-4-6", "claude-opus-4-6", "claude-opus-4-5",
                "claude-opus-4-1", "claude-opus-4-0", "claude-sonnet-4-0", "claude-3")
# Models with the effort parameter (output_config.effort).
_EFFORT_OK = ("claude-fable", "claude-mythos", "claude-opus-5", "claude-sonnet-5", "claude-haiku-5",
              "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6", "claude-opus-4-5", "claude-sonnet-4-6")


def _env(name: str, default: str) -> str:
    v = os.environ.get(name)
    return v if v not in (None, "") else default


@dataclass
class AIConfig:
    mode: str = field(default_factory=lambda: _env("JANA_AI_MODE", "off"))
    provider: str = field(default_factory=lambda: _env("JANA_AI_PROVIDER", "anthropic"))
    model: str = field(default_factory=lambda: _env("JANA_AI_MODEL", "claude-fable-5-1"))   # the strongest; Haiku 5.5 is 100× cheaper
    effort: str = field(default_factory=lambda: _env("JANA_AI_EFFORT", "low"))
    batch_size: int = field(default_factory=lambda: int(_env("JANA_AI_BATCH", "20")))
    concurrency: int = field(default_factory=lambda: int(_env("JANA_AI_CONCURRENCY", "8")))
    timeout_s: float = field(default_factory=lambda: float(_env("JANA_AI_TIMEOUT_S", "60")))
    max_retries: int = field(default_factory=lambda: int(_env("JANA_AI_MAX_RETRIES", "2")))
    max_failures: int = field(default_factory=lambda: int(_env("JANA_AI_MAX_FAILURES", "3")))
    cooldown_s: float = field(default_factory=lambda: float(_env("JANA_AI_COOLDOWN_S", "300")))
    min_conf: float = field(default_factory=lambda: float(_env("JANA_AI_MIN_CONF", "0.80")))        # fallback routing
    keep_local_conf: float = field(default_factory=lambda: float(_env("JANA_AI_KEEP_LOCAL_CONF", "0.90")))
    send_draft: bool = field(default_factory=lambda: _env("JANA_AI_DRAFT", "1") == "1")
    mask_pii: bool = field(default_factory=lambda: _env("JANA_AI_MASK_PII", "1") == "1")
    base_url: str | None = field(default_factory=lambda: os.environ.get("OPENAI_BASE_URL"))


class AIError(RuntimeError):
    pass


class AIRefusal(AIError):
    pass


def request_params(model: str, effort: str) -> dict:
    """Model-dependent knobs for messages.create (see the module docstring)."""
    oc: dict = {"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}}
    if model.startswith(_EFFORT_OK):
        oc["effort"] = effort
    params: dict = {"output_config": oc}
    if model.startswith(_SAMPLING_OK):
        params["extra_body"] = {"temperature": 0.0}       # the SDK no longer defines `temperature`
    return params


_PHONE = re.compile(r"(?:(?:\+7|8|7)[\s\-()]*\d{3}[\s\-()]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2})(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def mask_pii(text: str) -> str:
    return _EMAIL.sub("[email]", _PHONE.sub("[тел]", text))


def max_tokens_for(n: int) -> int:
    """~130 output tokens per record of JSON + room for adaptive thinking at low effort; a batch that still overflows is
    split in half by the caller."""
    return min(32_000, 2_000 + 320 * n)


class Stats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.requests = self.records = self.failures = self.refusals = 0
        self.input_tokens = self.output_tokens = self.cache_write_tokens = self.cache_read_tokens = 0
        self.cost_usd = 0.0
        self.latency_s = 0.0

    def add(self, n_records: int, usage: dict, latency: float, cost: float | None) -> None:
        with self._lock:
            self.requests += 1
            self.records += n_records
            for k in ("input_tokens", "output_tokens", "cache_write_tokens", "cache_read_tokens"):
                setattr(self, k, getattr(self, k) + int(usage.get(k, 0) or 0))
            self.cost_usd += cost or 0.0
            self.latency_s += latency

    def fail(self, refusal: bool = False) -> None:
        with self._lock:
            self.failures += 1
            self.refusals += int(refusal)

    def as_dict(self) -> dict:
        with self._lock:
            d = {k: getattr(self, k) for k in ("requests", "records", "failures", "refusals", "input_tokens",
                                               "output_tokens", "cache_write_tokens", "cache_read_tokens")}
            d["cost_usd"] = round(self.cost_usd, 6)
            d["cost_per_1k_records_usd"] = round(1000 * self.cost_usd / self.records, 4) if self.records else None
            d["avg_latency_s_per_request"] = round(self.latency_s / self.requests, 2) if self.requests else None
            return d


class AIClient:
    def __init__(self, cfg: AIConfig | None = None, transport=None) -> None:
        """transport(items, cfg) -> (records, usage): injectable for tests and for the Batch API path."""
        self.cfg = cfg or AIConfig()
        self.stats = Stats()
        self._transport = transport
        self._sdk = None
        self._lock = threading.Lock()
        self._consecutive_failures = 0
        self._open_until = 0.0

    # ------------------------------------------------------------------ availability
    def available(self) -> tuple[bool, str]:
        if self._transport is not None:
            return self._circuit_ok()
        if self.cfg.provider == "anthropic":
            try:
                import anthropic  # noqa: F401
            except ImportError:
                return False, "anthropic SDK not installed (pip install anthropic)"
            if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
                    or _env("JANA_AI_ASSUME_CREDENTIALS", "0") == "1"):
                return False, ("no credentials: set ANTHROPIC_API_KEY "
                               "(or JANA_AI_ASSUME_CREDENTIALS=1 after `ant auth login`)")
        elif self.cfg.provider == "openai-compatible":
            try:
                import openai  # noqa: F401
            except ImportError:
                return False, "openai SDK not installed"
            if not self.cfg.base_url:
                return False, "OPENAI_BASE_URL not set"
        else:
            return False, f"unknown provider {self.cfg.provider!r}"
        return self._circuit_ok()

    def _circuit_ok(self) -> tuple[bool, str]:
        if time.time() < self._open_until:
            return False, f"circuit open for {int(self._open_until - time.time())} s after {self.cfg.max_failures} failures"
        return True, "ok"

    def _record_outcome(self, ok: bool) -> None:
        with self._lock:
            if ok:
                self._consecutive_failures = 0
            else:
                self._consecutive_failures += 1
                if self._consecutive_failures >= self.cfg.max_failures:
                    self._open_until = time.time() + self.cfg.cooldown_s
                    self._consecutive_failures = 0

    # ------------------------------------------------------------------ transports
    def _anthropic(self, items: list[dict]) -> tuple[list[dict], dict]:
        import anthropic
        if self._sdk is None:
            self._sdk = anthropic.Anthropic(max_retries=self.cfg.max_retries, timeout=self.cfg.timeout_s)
        resp = self._sdk.messages.create(
            model=self.cfg.model,
            max_tokens=max_tokens_for(len(items)),
            system=[{"type": "text", "text": system_prompt(), "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_message(items)}],
            **request_params(self.cfg.model, self.cfg.effort),
        )
        u = resp.usage
        usage = {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                 "cache_write_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
                 "cache_read_tokens": getattr(u, "cache_read_input_tokens", 0) or 0}
        if resp.stop_reason == "refusal":
            raise AIRefusal("the model declined this batch")
        if resp.stop_reason == "max_tokens":
            raise _Overflow(usage)
        text = next(b.text for b in resp.content if b.type == "text")
        return json.loads(text)["records"], usage

    def _openai_compatible(self, items: list[dict]) -> tuple[list[dict], dict]:
        from openai import OpenAI
        if self._sdk is None:
            self._sdk = OpenAI(base_url=self.cfg.base_url, api_key=os.environ.get("OPENAI_API_KEY", "none"),
                               timeout=self.cfg.timeout_s, max_retries=self.cfg.max_retries)
        resp = self._sdk.chat.completions.create(
            model=self.cfg.model, temperature=0, seed=0, max_tokens=max_tokens_for(len(items)),
            messages=[{"role": "system", "content": system_prompt()}, {"role": "user", "content": user_message(items)}],
            response_format={"type": "json_schema", "json_schema": {"name": "records", "schema": OUTPUT_SCHEMA, "strict": True}},
        )
        usage = {"input_tokens": getattr(resp.usage, "prompt_tokens", 0), "output_tokens": getattr(resp.usage, "completion_tokens", 0)}
        if resp.choices[0].finish_reason == "length":
            raise _Overflow(usage)
        return json.loads(resp.choices[0].message.content)["records"], usage

    def _send(self, items: list[dict]) -> tuple[list[dict], dict]:
        if self._transport is not None:
            return self._transport(items, self.cfg)
        if self.cfg.provider == "openai-compatible":
            return self._openai_compatible(items)
        return self._anthropic(items)

    # ------------------------------------------------------------------ public
    def run_batch(self, items: list[dict]) -> dict[str, dict]:
        """One request (split on overflow). Returns {id: record}; raises AIError on failure."""
        t0 = time.perf_counter()
        try:
            recs, usage = self._send(items)
        except _Overflow as ov:
            self.stats.add(0, ov.usage, time.perf_counter() - t0, self._cost(ov.usage))
            if len(items) == 1:
                raise AIError("max_tokens on a single record") from None
            half = len(items) // 2
            return {**self.run_batch(items[:half]), **self.run_batch(items[half:])}
        dt = time.perf_counter() - t0
        self.stats.add(len(items), usage, dt, self._cost(usage))
        return {str(r.get("id")): r for r in recs if isinstance(r, dict)}

    def _cost(self, usage: dict) -> float | None:
        if self.cfg.provider != "anthropic" and self._transport is None:
            return 0.0
        return cost_usd(self.cfg.model, usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                        usage.get("cache_write_tokens", 0), usage.get("cache_read_tokens", 0))

    def parse(self, items: list[dict]) -> tuple[dict[str, dict], dict[str, str]]:
        """items: [{"id", "raw_address", "draft"?}]. Returns ({id: ai_record}, {id: error}) — never raises."""
        if self.cfg.mask_pii:
            items = [{**it, "raw_address": mask_pii(it["raw_address"])} for it in items]
        batches = [items[i:i + self.cfg.batch_size] for i in range(0, len(items), self.cfg.batch_size)]
        results: dict[str, dict] = {}
        errors: dict[str, str] = {}

        def work(batch: list[dict]) -> None:
            ok, why = self._circuit_ok()
            if not ok:
                errors.update({it["id"]: why for it in batch})
                return
            try:
                got = self.run_batch(batch)
                self._record_outcome(True)
                results.update(got)
                for it in batch:
                    if it["id"] not in got:
                        errors[it["id"]] = "missing in the AI answer"
            except AIRefusal as e:
                self.stats.fail(refusal=True)
                errors.update({it["id"]: f"refusal: {e}" for it in batch})
            except Exception as e:  # noqa: BLE001 — network, auth, schema: the record keeps its local answer
                self.stats.fail()
                self._record_outcome(False)
                errors.update({it["id"]: f"{type(e).__name__}: {str(e)[:160]}" for it in batch})

        if len(batches) == 1 or self.cfg.concurrency <= 1:
            for b in batches:
                work(b)
        else:
            with ThreadPoolExecutor(max_workers=min(self.cfg.concurrency, len(batches))) as pool:
                list(pool.map(work, batches))
        return results, errors

    def status(self) -> dict:
        ok, why = self.available()
        return {"available": ok, "reason": why, "provider": self.cfg.provider, "model": self.cfg.model,
                "effort": self.cfg.effort, "default_mode": self.cfg.mode, "batch_size": self.cfg.batch_size,
                "concurrency": self.cfg.concurrency, "min_conf": self.cfg.min_conf,
                "keep_local_conf": self.cfg.keep_local_conf, "prompt_version": prompt_version(),
                "sampling": "temperature=0" if self.cfg.model.startswith(_SAMPLING_OK) or self.cfg.provider != "anthropic"
                            else "omitted (rejected by this model)",
                "stats": self.stats.as_dict()}


class _Overflow(Exception):
    def __init__(self, usage: dict) -> None:
        super().__init__("max_tokens")
        self.usage = usage


_CLIENT: AIClient | None = None


def get_client() -> AIClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = AIClient()
    return _CLIENT


def set_client(client: AIClient | None) -> None:
    """Tests and the eval CLI swap the process-wide client."""
    global _CLIENT
    _CLIENT = client
