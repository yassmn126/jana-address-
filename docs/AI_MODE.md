# AI mode — Claude as a second annotator, on request

The scored answer always comes from the local pipeline (rules ⊕ char-CNN+BiLSTM+CRF ⊕ OSM gazetteer). The brief requires
it: slide 11, «Docker, автономная работа без сети». The jury runs our image with no network, so an API call on every
request can never be the scored path. AI mode is an **opt-in layer** on top of it, for the demo and for production at Jana
Post. It is off by default and does nothing in the jury image, which has no `anthropic` package and no key.

| mode (`?ai=` or env `JANA_AI_MODE`) | what goes to the model | when to use |
|---|---|---|
| `off` (default) | nothing | jury run, offline machines |
| `foreign` | only addresses detected as outside Kazakhstan | international parcels |
| `fallback` | foreign + records with calibrated confidence < 0.80 (`JANA_AI_MIN_CONF`) | **production**: the AI reviews only the uncertain tail |
| `always` | every record, with the local answer attached as a draft | audits, labeling, "AI on every request" demos |

Endpoints: `POST /api/v1/parse/batch?ai=fallback` (also `/parse/batch/file`, `/parse`), `GET /api/v1/ai/status`
(model, effort, prompt version, tokens and USD spent since start). The default response keeps **exactly** the jury
schema. `?explain=1` adds `country` and `ai: {used, base, agree, changed, invalid, notes}`.

## The request, knob by knob (`api/jana_address/llm/`)

| knob | value | why |
|---|---|---|
| model | `claude-fable-5-1` (env `JANA_AI_MODEL`) — the strongest model, the demo default | `claude-opus-5-5` and `claude-haiku-5-5` (100× cheaper, built for high-volume extraction) are one env var away; `make ai-cost` prints what each costs on the 3k file |
| prompt | `prompts/runtime_system.md` + 42 convention examples + 16 runtime examples, about 9k tokens, versioned by sha256 (`prompt_version`) | every rule of `docs/CONVENTIONS.md`, a step-by-step procedure, canonical tables, special cases, international rules |
| output | structured outputs: `output_config.format` = JSON schema (13 nullable fields per record) | the model can only emit parseable JSON with exactly our keys |
| effort | `low` (env `JANA_AI_EFFORT`) | Claude 5.x thinks adaptively. Effort is the one control of thinking depth, and thinking is billed as output |
| temperature | **not sent** to Haiku/Sonnet/Opus 5.5 | these models return **400** for any non-default `temperature`/`top_p`/`top_k`, and the SDK has removed the argument. Sent as `0` only to models that still accept it (Haiku 4.5, Sonnet 4.5/4.6, Opus ≤ 4.6) and to a local vLLM model |
| caching | `cache_control` on the **system block** | the prompt is written once per 5 min, then read at 0.05–0.1x. A top-level automatic breakpoint lands on the per-batch message and re-writes everything at 1.25x. `training/label_llm.py` and `eval/llm_generate.py` had exactly that, now fixed |
| batching | 20 records per request, 8 requests in parallel | amortizes the prompt and latency. A batch that hits `max_tokens` is split in half |
| failures | SDK retries 429/5xx. After 3 consecutive failures the circuit opens for 5 min | the service never blocks on the API. Records keep their local answer with `ai.error` |
| privacy | phones and e-mails are masked before sending | conventions ignore them anyway |
| validation | every AI value must pass the copy-not-rewrite span check, the canonical province list, the closed street-type list and the number patterns | an AI value that "rewrites" the text is dropped and the local value is kept |

**Determinism without temperature.** `temperature = 0` never guaranteed identical outputs, even on older models. On
Claude 5.x the stable answer comes from four things together: the schema, the explicit rules and examples, low effort,
and the validators that reject any value that is not in the input.

## Merge policy (`llm/policy.py`)

* foreign address → AI values. The local grammar and gazetteer are Kazakhstan-only.
* local confidence ≥ 0.90 (`JANA_AI_KEEP_LOCAL_CONF`) → local values. A disagreement only lowers the confidence.
  Measured on 416 gold rows, Opus 5.5 vs our parser: when they disagreed, our parser was right 25 times out of 32.
* otherwise → AI values, with gazetteer enrichment (province/district filled from the city) kept where the AI left a gap.
* confidence: agreement → ≥ 0.95. Disagreement → 0.60 (kept local) or 0.70 (took AI), both below the review threshold.

## International addresses

Offline, `api/jana_address/countries.py` flags foreign addresses from explicit evidence only:
- a country phrase («Россия», «Uzbekistan», «Deutschland»);
- a known foreign city written as the settlement («г. Бишкек»), when nothing Kazakhstani is in the string.

Two things never count as evidence: a landmark name («ТЦ Москва», «кафе Грузия») and a Kazakhstani street named after a
foreign place («ул. Ташкентская»).

A flagged record gets `flags: ["foreign:KG"]` and confidence ≤ 0.30. Kazakhstani gazetteer fills are removed, and a
country word is never kept as the city. On the 8,492 Kazakhstani gold records in `data/` no record was flagged, and the
jury score on `data/hard3k` did not change.

Convention for foreign addresses (ours; not jury data):
- `province` = region/state as written;
- `city`, `district`, `street_name`, `house`, `apartment`, `entrance`, `floor` and `landmark` follow the same copy rule;
- `street_type` = the closest lemma, null when the type is glued into the name («Invalidenstraße»);
- `country` (explain mode only) = ISO 3166-1 alpha-2.

Test set: `data/hard3k/intl_s2026_gold.json`, 150 records from 12 countries.

## Cost

Prices are from <https://platform.claude.com/docs/en/about-claude/pricing>, in USD per million tokens:

| model | input | 5-min cache write | cache read | output |
|---|---|---|---|---|
| Haiku 5.5 | 0.10 | 0.125 | 0.01 | 0.50 |
| Sonnet 5.5 | 2 | 2.50 | 0.10 | 10 |
| Opus 5.5 | 4 | 5 | 0.20 | 20 |

Message Batches API: −50 %. `eval/ai_eval.py --dry-run` estimates any file; measured numbers replace the estimate after
a real run.

| per 1,000 addresses sent to the AI | Haiku 5.5 | Sonnet 5.5 | Opus 5.5 |
|---|---|---|---|
| sync (estimate, 140 output tokens/record) | ≈ $0.09 | ≈ $1.73 | ≈ $3.45 |
| Batch API | ≈ $0.04 | ≈ $0.86 | ≈ $1.73 |

The 140 output tokens per record are measured, not guessed: Opus 5.5 at effort `medium` wrote 59,458–60,086 output tokens
for 416 records (`runs/verify*`, 2026-10-18).

On the nationwide hard3k set, `fallback` sends **1.8 %** of the records. That is cheap: for 1,000,000 addresses a month it
is about $2 on Haiku 5.5 or $32 on Sonnet 5.5. But it reaches only **23 of the 134** records the parser gets wrong.

The reason is the calibrated confidence. It was fitted on the old dev sets and is over-confident on this new
distribution: 100 of the 134 wrong records still report 0.99. So in production, either:
- run `always` on Haiku 5.5, about **$90 a month** per million addresses; or
- refit `api/artifacts/calibrator.json` on hard3k-style data before trusting `fallback`.

| threshold on confidence | routed to AI | parser errors it reaches |
|---|---|---|
| < 0.80 (default) | 1.8 % | 17 % |
| < 0.95 | 3.1 % | 19 % |
| < 0.97 | 6.8 % | 25 % |
| always | 100 % | 100 % |

## Run it

```bash
pip install anthropic                     # not in the jury image on purpose
export ANTHROPIC_API_KEY=...              # never commit it; or `ant auth login` + JANA_AI_ASSUME_CREDENTIALS=1
JANA_AI_MODE=fallback make api-dev        # or per request: POST /api/v1/parse/batch?ai=fallback
python -m eval.ai_eval --gold data/hard3k/hard3k_s2026_gold.json --mode always --dry-run           # cost, no call
python -m eval.ai_eval --gold data/hard3k/hard3k_s2026_gold.json --mode fallback --limit 600       # real, ~$0.01
python -m eval.ai_eval --gold data/hard3k/intl_s2026_gold.json --mode foreign                     # international
```

### Bring your own key (no server-side credentials)

The hosted demo may run with no `ANTHROPIC_API_KEY` at all. Every endpoint that can reach Claude (`/api/v1/verify`,
`/api/v1/conflicts/scan`, the parse endpoints with `?ai=…`, `/api/v1/ai/status`) also reads the caller's own key from
the request headers `X-Anthropic-Api-Key` and, for a user-scoped `sk-ant-usr-…` key, `X-Anthropic-Workspace-Id`.
`client_for()` in `llm/client.py` builds a transient client around that key for the one request (sharing the
process-wide cost meter); nothing is persisted or logged, the key is never accepted in a URL, and `status()` never
echoes it. The web UI has the matching field («Ключ Anthropic», `web/src/components/AiKey.tsx`): the key is kept in
that browser's localStorage and attached only to calls whose provider is Claude. Without any key `/verify` answers
`401` with a hint; a rejected key maps to `401` too (`AuthenticationError`, or a user-scoped key without a workspace).

```bash
curl -s -X POST localhost:8000/api/v1/verify?provider=anthropic -H 'Content-Type: application/json' \
     -H "X-Anthropic-Api-Key: $ANTHROPIC_API_KEY" -H "X-Anthropic-Workspace-Id: $ANTHROPIC_WORKSPACE_ID" \
     -d '[{"id":"1","raw_address":"Алматы, ул. Абая 10, кв 5"}]'
```

A local model on the university GPU works with the same prompt: `JANA_AI_PROVIDER=openai-compatible
OPENAI_BASE_URL=http://<gpu>:8000/v1 JANA_AI_MODEL=<served name>` (vLLM, `temperature=0`, JSON-schema guided decoding).
That path keeps the data on campus.
