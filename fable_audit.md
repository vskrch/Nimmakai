# Nimmakai (`nimmakai/auto`) — Production-Readiness Audit

**Auditor:** Staff-engineer two-pass audit (structural + adversarial)
**Date:** 2026-07-19
**Scope:** classification, routing/selection, caching, cache invalidation, OpenAI compat surface, latency, concurrency under Cursor/Cline tool-calling workloads.
**Bar:** OpenRouter-level reliability, latency, and polish.

---

## 1. Executive Summary

**Verdict: NOT production-ready for the stated bar.** The happy path for streaming `/v1/chat/completions` is genuinely good — TTFT-based fail-fast failover, per-key RPM/quarantine pooling, health-adaptive chain re-ranking, and reasoning→content normalization for Cursor are all real and correctly wired. But the audit found that **two of the four OpenAI endpoints (`/v1/completions`, `/v1/responses`) are broken by design on every non-streaming request**, transport-level provider failures **bypass the fallback chain entirely** and surface FastAPI's default error shape, several failure paths return **OpenAI-incompatible or empty error bodies**, and the flagship OpenRouter-parity features (session stickiness, `plugins` auto-router controls) are **dead code at runtime** because the sanitizer strips their fields before the parser runs. There is **no caching layer** for responses, route plans, or the model catalog; the only caches that exist are an LLM-classify LRU (off by default), a SQLite rankings cache with **unbounded staleness**, a catalog disk snapshot, and an in-memory sticky-session store whose effect is silently destroyed downstream.

### Coverage statement (what was and was not examined)
- **Read in full:** `routes/openai.py`, `upstream.py`, `compat.py`, `main.py`, `auth.py`, `config.py`, `balancer.py`, `resilience.py`, all of `routing/` (`classifier.py`, `intents.py`, `selector.py`, `auto_router.py`, `optimizer.py`, `fallback.py`), all of `safety/` (`guard.py`, `sticky.py`, `concurrency.py`, `circuit_breaker.py`, `backoff.py`, `jitter.py`, `budgets.py`), and `catalog/` core (`registry.py`, `ladder.py`, `health.py`, `hub.py`, `providers.py`, `learning.py`, `families.py`, `aliases.py`, `db.py` partial). Analytics hot-path code (`context.py`, `writer.py`, `store.py` hot methods) examined where it touches the request path.
- **Not examined / does not exist:** There is **no dedicated response/route caching layer to review** — Section 3 designs one from scratch. `routes/admin.py`, `routes/analytics.py`, the frontend, `prober.py`, and `docs_fetcher.py` were reviewed only where they intersect the request lifecycle. This is a static audit; no load tests were executed. Findings below trace to specific lines actually read.

### Request lifecycle (as actually coded)
```
client → FastAPI /v1/{chat/completions|completions|responses|embeddings}
  → request.json() → sanitize_chat_body()            [compat.py — strips fields]
  → _prepare_routed():
      auth (require_proxy_auth)
      classify (IntentClassifier.classify — rules; optional LLM assist)
      guard.before_request (global gate + sticky lookup)
      selector.resolve → RouteDecision{chain, mode, intent}  [ladder cache + optimize_chain + pin]
  → FallbackExecutor.execute_stream/execute_json:
      _chain(decision) [availability filter + optimize_chain AGAIN + hot/cold split]
      per model: hub.client_for_model → KeyPool.acquire → httpx (retries/backoff) → advance on failure
  → normalize_sse_stream / normalize_completion_json  [compat rewrite]
  → StreamingResponse/JSONResponse + X-Nimmakai-* headers
  → finally: guard.after_request (gate release + sticky pin), trace enqueue
```

### Top 5 risks, ranked by severity × likelihood
1. **F-01 — `/v1/completions` and `/v1/responses` (non-streaming) soft-fail on every success and fan out to the entire model chain** (up to 12 upstream calls per request), return the *last* model's answer, and poison the learning store with false failures for every model tried. Critical × always-occurs.
2. **F-13 — Blocking work on the asyncio event loop in the request path** (synchronous `learning.json` file writes, per-request SQLite reads contending with the analytics writer thread's batch-commit lock, full-conversation regex scans of ~100KB–1MB Cursor payloads before the tools short-circuit, per-SSE-chunk JSON re-serialization). Every stall freezes *all* concurrent streams. High × continuous.
3. **F-03 — Transport-level upstream failures (`httpx.ConnectError`, `ReadTimeout`) skip model fallback entirely** — FallbackExecutor catches only `RuntimeError` — and surface as FastAPI's `{"detail": "Internal Server Error"}`, a non-OpenAI shape. One provider with DNS/socket problems takes down requests routed to it despite 11 healthy fallbacks. Critical × common under real network weather.
4. **F-08 — Session stickiness and explicit-model head ordering are silently destroyed**: `FallbackExecutor._chain()` re-sorts the chain by live score after the selector pins the sticky/requested model first. Multi-turn Cursor sessions hop models turn-to-turn (breaking upstream KV/prompt-cache locality and risking strict-provider 400s on foreign `tool_call_id` formats), and a client that names a healthy model can be served a different one with zero failures. High × every agent session.
5. **F-05/F-06/F-17 — Streaming failure envelope is broken**: all-models-TTFT-stall returns **HTTP 200 with an empty body**; exhausted 429/401 streams return **empty error bodies**; mid-stream upstream failure emits a bare `data: [DONE]` with **no finish_reason chunk or error event**, presenting truncated tool-call JSON as complete output. Critical × moderate (provider brownouts — precisely when reliability matters).

Honorable mentions: F-04 (all auth errors are nested under `{"detail": ...}` — OpenAI SDKs cannot parse them), F-02 (OpenRouter `plugins`/`session_id` parsing is dead), F-11 (no end-to-end deadline; worst case minutes, guaranteed H12 kills behind Heroku's 30s router per the `Procfile`).

---

## 2. Findings Table

Severity honors the audit constraint: *anything returning OpenAI-incompatible error/response shapes is Critical regardless of fix size.* Effort: S (<½ day), M (≤2 days), L (>2 days).

| ID | Area | Finding | Issue (root cause) | Suggested Fix | Severity | Effort |
|----|------|---------|--------------------|---------------|----------|--------|
| F-01 | routing | Every non-streaming `/v1/completions` and `/v1/responses` request fans out to the full chain and returns the last model's output. **Repro:** POST `/v1/completions` `{"model":"auto","prompt":"hi"}` against a ≥2-model chain; observe N upstream spans, `X-Nimmakai-Fallback-Index: N-1`, and `empty_replies` incremented for every model in `learning.json`. | `_analyze_success_body` (`fallback.py:176-197`) requires `choices[0].message` to be a dict. Text completions have `choices[0].text`; Responses API bodies have `output` and no `choices` → `empty_reply=True` → soft-fail advance at `fallback.py:561-571` on every success. Learning store records false failures (`registry.py:549-558` → `learning.py:99-119`). | Make `_analyze_success_body` schema-aware: accept `choices[0].text` (completions) and `output[*].content` / `output_text` (responses); only apply the tool-call soft-fail check when the response schema is chat. | Critical | S |
| F-02 | routing | OpenRouter/Kilo body controls (`session_id`, `plugins.allowed_models`, `cost_quality_tradeoff`, `models[]` partially) are dead — parsed after they've been stripped. **Repro:** POST with `{"session_id":"s1","plugins":[{"id":"auto-router","allowed_models":["deepseek/*"]}]}`; chain is unfiltered and no sticky binding is created from the body. | `sanitize_chat_body` runs first (`openai.py:519`) and `_STRIP_BODY_KEYS` (`compat.py:20-34`) removes `session_id`/`sessionId`/`plugins` before `parse_auto_router_options` (`openai.py:239`) and `sticky.resolve_session_id(body=…)` (`sticky.py:121-124`) ever see them. The comment "parse before strip" at `openai.py:238` documents the violated intent. | Parse `AutoRouterOptions` and resolve the session id from the **raw** body in `_chat_like` before `sanitize_chat_body`; pass both into `_prepare_routed`. Keep `strip_router_client_fields` as the single stripper. | High | S |
| F-03 | routing + compat | Transport exceptions bypass model fallback and return FastAPI's default 500 shape. **Repro:** point one provider's `base_url` at a black-holed host; send a request that routes to its model → ~90s of connect retries (30s connect × 3), then `{"detail":"Internal Server Error"}`; chain never advances; circuit breaker untouched. | `UpstreamClient` re-raises the original `httpx` exception after retries (`upstream.py:200-201`, `359-360`); `FallbackExecutor` catches only `RuntimeError` (`fallback.py:435`, `727`); `_chat_like` re-raises unknown exceptions (`openai.py:858-871`) → Starlette 500. | Catch `httpx.HTTPError` (and `OSError`) alongside `RuntimeError` in both `execute_json` and `execute_stream`, record the outcome, and advance the chain; add a global exception handler that emits `{"error":{"message","type":"server_error","code"}}`. | Critical | S |
| F-04 | compat | All auth failures return `{"detail": {"error": {...}}}` — the OpenAI error object is nested under `detail`, so OpenAI SDKs/Cursor can't read `error.message`. **Repro:** `curl /v1/chat/completions` with no key → body starts `{"detail":`. | `validate_proxy_token` raises `HTTPException(detail={"error": ...})` (`auth.py:29-69`); FastAPI wraps `detail` verbatim. No custom exception handler exists (verified by grep). | Register an `HTTPException` handler that unwraps dict details into the top-level body (and add `WWW-Authenticate`); one handler fixes every auth/HTTP error site. | Critical | S |
| F-05 | routing + compat | If every chain model opens a stream but stalls past TTFT (or errors at open), the client receives **HTTP 200 with a zero-byte body**. **Repro:** stub upstreams that return 200 + never send bytes; POST `stream:true` → 200, immediate EOF. Cursor spins on an empty response. | `last_status` is set to the 2xx status at `fallback.py:746`; TTFT-timeout and open-failure paths `continue` (`fallback.py:767-822`); after the loop the terminal return reuses `last_status` with an empty iterator (`fallback.py:1022-1030`). | In the terminal return, force `status_code=504` (or 502) with an OpenAI error JSON body when no stream was successfully relayed. | Critical | S |
| F-06 | compat | Streaming requests that exhaust retries on 429/401/403 return that status with an **empty body** (no `{"error": ...}`). **Repro:** force 429 from all keys on all chain models with `stream:true`. | `upstream.stream` returns empty iterators for terminal 429/401 (`upstream.py:269-299`); `execute_stream` relays `err_raw=b""` (`fallback.py:953-1015`). | Synthesize an OpenAI error body (include `Retry-After` when present) whenever the upstream error body is empty. | Critical | S |
| F-07 | routing | Key `in_flight` slots leak permanently when header construction fails after a stream opens; 3 leaks make a key unusable until restart. **Repro (race):** open circuit for a provider (5 concurrent failures) while another request has just opened a stream on it → `routing_headers` → `hub.client_for_model` raises (`hub.py:179-181`) → `except RuntimeError` (`openai.py:845`) returns without ever consuming `result.byte_iter`, so `pool.release` in the iterator's `finally` (`upstream.py:343-352`) never runs; `_is_available` blocks the key at `in_flight >= max_in_flight_per_key` (`balancer.py:118`). | `routing_headers` (`fallback.py:383-385`) calls the raising `client_for_model` between stream-open (`openai.py:577`) and response start (`openai.py:667`). | Make `routing_headers` non-raising (reuse the provider id already resolved during `execute_stream`, stored on the result), and wrap the post-open section in `try/except` that closes `result.byte_iter` on any failure. | Critical | S |
| F-08 | routing | Sticky-session model pins and explicit/requested-model head ordering are silently discarded, so multi-turn Cursor sessions hop models and explicit model requests can be served by a different healthy model. **Repro:** two identical multi-turn requests with `x-session-id`; inspect `X-Nimmakai-Model` — changes whenever live scores shift; or request a known healthy low-score model and observe a different `X-Nimmakai-Model` with `fallback_index=0`. | Selector pins after ranking (`selector.py:356-359`, `auto_router.py:248-260`) and places explicit models at head (`selector.py:270-297`), but `FallbackExecutor._chain` re-runs `optimize_chain` over the whole chain (`fallback.py:339-353`), a pure score sort. | Carry `pinned_head: str | None` on `RouteDecision`; in `_chain`, re-rank only the tail and keep the pinned head first unless it is unhealthy/in-cooldown (then log + demote). Honors "never surprise an explicit model request" and restores OpenRouter-style stickiness. | High | S |
| F-09 | compat | Global concurrency-gate exhaustion returns a generic FastAPI 500 instead of the intended 503 `nimmakai_pool_exhausted`; the gate is also sized only to the default provider's pool, throttling multi-provider deployments. **Repro:** `GLOBAL_MAX_IN_FLIGHT=1`, two concurrent slow requests → second gets `{"detail":"Internal Server Error"}` after 30s. | `gate.acquire` raises `RuntimeError` (`concurrency.py:28-32`) inside `_prepare_routed`, whose catch-all re-raises non-HTTP exceptions (`openai.py:527-540`) — the friendly 503 handler only wraps the later phase (`openai.py:845-857`). Sizing at `guard.py:29-31` uses `len(pool)` of the default pool only. | Catch `RuntimeError` from `_prepare_routed` and return `guard.pool_exhausted_error()` with 503 + `Retry-After`; size the gate from the sum of active provider pools (recompute on provider upsert). | Critical (shape) | S |
| F-10 | routing | Gate slot leaks permanently if anything between `before_request` and `_prepare_routed`'s return raises (e.g. `strict_catalog` `RuntimeError` at `registry.py:504-505`, selector bugs). Each leak permanently shrinks capacity. | `guard.before_request` acquires the gate at `openai.py:278`; exceptions from `selector.resolve`/span collection (`openai.py:283-307`) propagate without any `after_request`. | Wrap post-acquire logic in `try/except` that releases the gate on failure (or acquire the gate last, after routing decision). | High | S |
| F-11 | routing | No end-to-end deadline: worst case = 30s gate wait + per-model (30s pool acquire + 3 retries with backoff + 30s connects) × 12 models — minutes. Behind Heroku (`Procfile`) the router kills the connection at 30s with no response bytes (H12), so clients see a reset, not an error. | `request_deadline_seconds=180` exists (`config.py:97`) but has zero call sites (verified by grep). `KeyPool.acquire` defaults `max_wait=30` per call (`balancer.py:143`), multiplied across chain hops. | Thread a monotonic deadline through `_prepare_routed` → `FallbackExecutor` → `pool.acquire(max_wait=remaining)`; stop advancing the chain when < ~8s remain and return a 504 OpenAI error. | High | M |
| F-12 | routing | Pre-first-byte fallback cascades sleep up to ~45s+ with zero bytes to the client: inter-model backoff scales with **chain index**, not per-provider retry count (hop 5 sleeps ~8–19s), and headers aren't sent until a stream opens. | `sleep_backoff(idx, …)` between different models (`fallback.py:617-650` JSON, `983-999` stream); `StreamingResponse` created only after `execute_stream` returns (`openai.py:667`). | For streams: skip inter-model backoff when the next candidate is a different provider (rate-limit domains are independent) and cap same-provider backoff at 2s; combined with F-11's deadline this bounds TTFB. (OpenRouter-style early-200 + heartbeat comments is not needed once hunting is bounded to <20s.) | High | S |
| F-13 | routing (perf) | Event-loop blocking on the hot path: (a) `learning.save()` synchronously JSON-dumps and writes the whole store inside request handling (`learning.py:120-121` → `199-228`, called from `registry.record_outcome`, `registry.py:549-558`); (b) `cost_overrides_map()` does per-request SQLite under the same `threading.Lock` the analytics writer thread holds across whole batch commits (`openai.py:118-119`, `store.py:487-493`, `writer.py:146-158`) — the loop can block behind an fsync; (c) classifier joins + lowercases + regex-scans the entire conversation (Cursor: 100KB–1MB) *before* the `tools_present` short-circuit can win (`classifier.py:115-117`, `198-202`, `239-244`); (d) `normalize_sse_stream` JSON-parses and re-serializes **every** SSE line because the fast path requires `"model"` to be absent and it never is (`compat.py:158-163`); (e) `recompute_rankings` (full regex scoring of all models × 6 intents × 3 variants) runs on the loop (`registry.py:422-443` via `main.py:293-303`). | Multiple synchronous I/O and O(payload) CPU operations inside `async def` handlers; single-process asyncio means each stall freezes all concurrent streams. | (a) fire-and-forget `asyncio.to_thread(learning.save)`; (b) cache overrides in memory with write-through invalidation (see §3); (c) short-circuit on `tools`/`tool_choice`/roles before text feature extraction and cap scanned text at first+last 16KB; (d) rewrite `model` via one cheap byte-level substitution and parse JSON only when `reasoning` appears; (e) run rebuilds in `asyncio.to_thread` and swap atomically. | High | M |
| F-14 | routing | No context-length-aware routing: a 150k-char Cursor payload is happily routed to 16k-context models, guaranteeing 400s that are only recovered by fragile provider-message heuristics (`fallback.py:150-167`), burning seconds per hop; providers with unrecognized overflow wording return the 400 to the user. | `registry.context_by_model` exists (`registry.py:273-297`) and the classifier computes `char_len` (`classifier.py:219`), but neither `selector.resolve` nor `FallbackExecutor._chain` filters candidates by size. | In `_chain`, estimate tokens (`char_len/3.5 + max_tokens`) and drop models whose known context is smaller (keep models with unknown context); attach the estimate to `RouteDecision`. | High | S |
| F-15 | routing | 30s connect timeout defeats fail-fast: TTFT (≤12s) only starts after response headers; a connect-level stall burns up to 30s × 3 attempts before the chain advances. | `httpx.Timeout(self.timeout, connect=30.0)` (`upstream.py:59`). | Set `connect=5.0` (LLM providers answer TCP/TLS fast or not at all); keep read timeout long for streams. | Medium | S |
| F-16 | routing | Circuit breaker is a shallow feature: it never closes (`succeed()` has zero call sites — grep) and never sees real HTTP/transport failures (`fail()` only fires for missing runtime/keys, `hub.py:188-194`); `allow()` returns True forever in HALF_OPEN (`circuit_breaker.py:46-47`). A provider returning pure 5xx never trips it; a tripped provider latches half-open indefinitely. | Breaker wired only into `client_for_model`, not into request outcomes. | Call `circuit_breaker.fail(pid)` on transport errors/5xx and `circuit_breaker.succeed(pid)` on 2xx from `FallbackExecutor`; transition HALF_OPEN→CLOSED on success is already implemented in `succeed`. | Medium | S |
| F-17 | compat | Mid-stream upstream failure emits a bare `data: [DONE]` — no `finish_reason` chunk, no error event — so truncated output (including **partial tool-call JSON arguments**) is presented to Cursor/Cline as a complete response; the agent may execute a malformed tool call. | `robust_iter` exception path yields `[DONE]` only (`fallback.py:928-936`); `_gated_stream` does the same (`openai.py:610-621`). | Before `[DONE]`, emit a final chunk `{"choices":[{"delta":{},"finish_reason":"error"...}]}` plus an OpenAI-style `data: {"error": {...}}` event; never fabricate completeness. | High | S |
| F-18 | compat | A 2xx upstream that ignores `stream:true` and returns `application/json` is relayed as the "stream" — OpenAI SDKs expecting SSE fail to parse or hang. | No content-type check on the 2xx stream path (`fallback.py:748+`, media passthrough at `openai.py:590`). | If 2xx and content-type is JSON, read the body and convert it to a single-chunk SSE sequence (chunk + usage + `[DONE]`), as OpenRouter does. | Medium | S |
| F-19 | compat | Terminal upstream error bodies pass through raw: provider-specific shapes (`{"detail": ...}`, HTML/text pages → JSON-encoded *string* bodies via `resp.text`) reach OpenAI clients. | `request_json` returns `resp.text` for non-JSON (`upstream.py:170-174`); `execute_json` returns the last body verbatim (`fallback.py:613-615`, `652-653`); `normalize_completion_json` passes non-dicts through (`compat.py:81-84`); same for the routing-disabled path (`openai.py:806-844`). | Normalize any terminal ≥400 body that isn't `{"error": {...}}`-shaped into the OpenAI envelope with `code:"upstream_error"`, preserving the original as `error.metadata.raw`. | High | S |
| F-20 | compat | Silent request mutations: `n>1` forced to 1 with no error; `store`, `metadata`, `user`, and notably `prompt_cache_key` stripped (`compat.py:20-56`). Stripping `prompt_cache_key` actively hurts upstream prompt-cache hit rates (latency + cost) for providers that support it; `n` silently downgraded violates the contract. | One global strip list applied to all providers and all endpoints. | Pass `prompt_cache_key`/`user` through (OpenAI-compat servers ignore unknown fields); return a 400 OpenAI error for `n>1` instead of silently returning fewer choices. | Medium | S |
| F-21 | compat | `/v1/embeddings`: malformed JSON → unhandled exception → `{"detail": ...}` 500; body is parsed before auth, so unauthenticated clients can drive JSON parsing of arbitrarily large bodies (no size cap anywhere). | `body = await request.json()` unguarded at `openai.py:890`; auth happens later inside `_prepare_routed`. | Mirror `_chat_like`'s guarded parse + 400 shape; validate auth before parsing; add a global body-size limit middleware (e.g. 20MB). | Medium | S |
| F-22 | invalidation | Rankings/ladder cache staleness is unbounded: SQLite cache is imported frozen at boot with no age check (`registry.py:344-354`, `371-390`); the refresh loop passes `recompute_rankings=False` (`main.py:316-323`), so newly-live models never join non-coding chains (coding is rescued by `coding_candidates()`, `selector.py:343-348`) until an admin refresh or >30% fallback-advance rate (`fallback.py:92-96`). `QUALITY_TIERS` (`ladder.py:43-153`) hardcodes model families and will mis-rank new releases. | Sticky-rankings design has no TTL and no catalog-delta trigger in the hub refresh path. | Add `max_age` (24h) to the imported cache (recompute in background if older) and trigger recompute when live_ids gained models absent from all frozen ladders (the check exists in `_rebuild_all_chains` but that method isn't called from `refresh_from_hub`). | Medium | S |
| F-23 | routing | Implicit sticky fingerprint = hash of first system + first user message (`sticky.py:139-167`); Cursor's system prompt is identical across all conversations, so distinct chats with the same opening user message (or several users sharing one proxy key) share one pin/binding. | Fingerprint entropy too low for the dominant client. | Include message count + last-user-message prefix in the fingerprint basis. | Low | S |
| F-24 | classification | Classifier housekeeping: `AGENT_FINGERPRINTS` contains the substring `"cursor"` (`classifier.py:21-32`) — any chat mentioning DB cursors classifies as coding_agentic (confidence 0.92); `rules_then_llm` mode inserts a serial upstream LLM RTT before routing when confidence < 0.55. Low impact because tools_present dominates Cursor traffic and mode defaults to `rules_only`. | Over-broad substring fingerprints; serial LLM assist. | Tighten fingerprints to phrases (e.g. `"cursor ide"`, tool-schema names); keep LLM assist opt-in. | Low | S |
| F-25 | compat | Routing-disabled non-stream passthrough can return a JSON-encoded string body (e.g. upstream HTML error page) — a syntactically valid but shape-invalid response. | `openai.py:806-844` wraps whatever `request_json` returned in `JSONResponse`. | Covered by F-19's normalization (apply it on the passthrough path too). | Medium | S |
| F-26 | routing (perf) | Chain is fully re-ranked 2–3× per request: `selector._finalize_chain` → `health_reorder` → `optimize_chain` (`registry.py:508-521`), then `FallbackExecutor._chain` → `optimize_chain` again (`fallback.py:339-348`). Sub-ms each, but duplicated on every request. | Layering evolved; both layers defensively re-rank. | Rank once in `_chain` (single owner) with the §3 route-plan micro-cache; selector only assembles candidates + pin. | Low | S |
| F-27 | routing | Vision auto-requests with no vision-capable live models fall back to `sorted(live_ids)` — alphabetical — so images are sent to text-only models (nonsense output or provider 400). | `selector.py:171-178` generic emergency fallback ignores intent modality. | If intent is vision and the ladder is empty, return a 400 OpenAI error (`code:"no_vision_model"`) instead of routing blind. | Low-Med | S |

---

## 3. Caching Design Proposal

### Context
Today's only caches: LLM-classify LRU (`classifier.py:56-77`, off by default), SQLite sticky-rankings cache (no TTL — F-22), catalog disk snapshot, and the sticky-session store (defeated — F-08). There is **no** caching of route plans, `/v1/models` payloads, embeddings, or admin-config reads, and nothing that promotes **upstream** prompt-cache hits — the largest real latency lever for Cursor's multi-turn tool loops.

### Storage layer: in-process TTL-LRU dicts (one shared `TTLCache` utility), SQLite only for what must survive restart
Justification: the deployment is a **single uvicorn worker** (`Procfile: web: uvicorn nimmakai.main:app` — no `--workers`), so in-process memory gives ~100ns lookups with zero coherence problems. Redis/memcached would add a network RTT larger than everything being saved, plus an ops dependency, for a single-node gateway. The rankings cache already persists in SQLite and stays there (with the F-22 age fix). Nothing else needs durability: route plans and catalog payloads rebuild in milliseconds at boot.

### What gets cached, keys, TTLs

| # | Cache | Key composition | Value | TTL / bound | Invalidation triggers |
|---|-------|-----------------|-------|-------------|------------------------|
| 1 | **Route-plan micro-cache** (replaces the 2–3× per-request `optimize_chain` re-ranks) | `(intent, variant, sha1(sorted allowed_models), free_only, ladder.computed_at)` | final optimized chain (post health/hot-cold ordering), max ~30 entries | **1.0s** | TTL only — 1s staleness is far inside the shortest health window (15s 429-cooldown, 45s model cooldown), so a dying model is demoted at most 1s late; sticky pin is applied *after* cache read, per request |
| 2 | **`/v1/models` payload** | `sha256(sorted(live_ids) + ladder.computed_at + inject_auto_model)` → doubles as **ETag** | serialized JSON bytes | 30s backstop | event-driven: end of `refresh_from_hub`, provider upsert/remove/enable, rankings recompute. Serve `304 Not Modified` on `If-None-Match` — Cursor re-polls models on settings open |
| 3 | **Embeddings response cache** | `sha256(resolved_model + canonical_json(input) + dimensions + encoding_format)` | full success response body | 24h, LRU capped at 256MB / 50k entries | key is fully content-derived; model version changes produce new resolved ids → natural roll. Embeddings are deterministic per (model, input) — the one response type safe to cache. Cursor re-embeds identical file chunks constantly during indexing |
| 4 | **Admin-config reads** (`cost_overrides_map`, preferences) | static key per table | in-memory dict | ∞ | **write-through**: the admin mutation endpoints update the in-memory copy synchronously. Removes the per-request SQLite read that contends with the analytics writer's commit lock (F-13b) |
| 5 | **Classification** | *(not a new cache)* — reorder rules so `tools`/`tool_choice`/tool-role short-circuit **before** text feature extraction; cap scanned text at first+last 16KB. Keep the existing LLM-classify LRU, adding a `CLASSIFIER_VERSION` constant to its key so rule changes invalidate it | — | existing 600s | version-bump on classifier code changes |

### What is deliberately NOT cached
Full `/v1/chat/completions` responses. Agent traffic is temperature-varied, tool-loop-unique, and context-cumulative — realistic hit rate ≈ 0% and any hit is a correctness hazard (stale tool results replayed into a live session). Instead, maximize **upstream provider prompt-cache hits**, which is where OpenRouter wins its latency reputation:
- **Fix F-08** so a session's requests land on the same model *and* same API key (provider KV/prefix caches are per-account) — restoring `sticky_boost` key affinity plus the model pin.
- **Stop stripping `prompt_cache_key`** (F-20) and forward it upstream.
- Report `cached_tokens` (already extracted, `fallback.py:506-512`) on the analytics dashboard to verify the hit rate.

### Expected impact
- Proxy-added latency p50: ~3–8ms → **<1ms** (route-plan cache + config cache + classifier cap + SSE fast path), and eliminates the recurring whole-loop stalls from F-13.
- `/v1/models`: ~10–50ms of per-call enrichment → **<1ms / 304**.
- Embeddings during Cursor indexing: 30–70% hit rate → those calls drop from 100–500ms to <1ms and stop consuming RPM budget.
- Multi-turn chat TTFT: 20–40% improvement via upstream prefix-cache affinity (provider-dependent), plus `cached_tokens` cost discounts.
- Correctness risk: none material — every key is content/version-derived; the only time-based staleness (route plan, 1s) is bounded well inside health-cooldown windows.

---

## 4. Implementation Tickets

### TICKET-1: Schema-aware success analysis for `/completions` and `/responses`
**Priority:** Critical
**Area:** routing
**Problem:** `_analyze_success_body` only understands chat-completion bodies, so every successful non-streaming `/v1/completions` and `/v1/responses` call is marked `empty_reply` and soft-failed. Every such request fans out across the entire chain (up to 12 upstream calls), returns the last model's output, and records false failures for every model in the learning store, corrupting future rankings.
**Fix:** In `fallback.py`, detect the response schema: chat (`choices[0].message`), text (`choices[0].text`), responses (`output` / `output_text`). Compute `empty_reply`/`tool_ok` per schema; apply tool-call checks only for chat/responses schemas. Pass the request path (already available as `path`) into the analysis to disambiguate.
**Acceptance criteria:**
- Non-streaming `/v1/completions` with a 2-model chain makes exactly 1 upstream call on success; `X-Nimmakai-Fallback-Index: 0`.
- Same for `/v1/responses` with an `output`-shaped body.
- Chat soft-fail behavior (empty content + no tool_calls → advance) unchanged; `tests/test_fallback.py::test_soft_fail_empty_reply_advances` still passes.
- Learning store shows no `empty_replies` increment for successful text/responses calls.
**Files likely affected:** `src/nimmakai/routing/fallback.py`, `tests/test_fallback.py`

### TICKET-2: Parse auto-router/session fields from the raw body, before sanitization
**Priority:** High
**Area:** routing
**Problem:** `sanitize_chat_body` strips `session_id`, `sessionId`, and `plugins` before `parse_auto_router_options` and the sticky-session resolver read the body, so OpenRouter-parity controls (allowed_models, cost_quality_tradeoff, body-based session stickiness) are dead code despite being advertised.
**Fix:** In `_chat_like` (and `/embeddings`), capture `auto_opts = parse_auto_router_options(raw_body)` and `session_id` before calling `sanitize_chat_body`; pass them into `_prepare_routed` and `guard.before_request` explicitly. Remove the duplicated router fields from `_STRIP_BODY_KEYS` so `strip_router_client_fields` is the single owner.
**Acceptance criteria:**
- A request with `plugins:[{id:"auto-router",allowed_models:["deepseek/*"]}]` produces a chain containing only matching models (header-verifiable).
- `session_id` in the body creates a sticky binding: two sequential requests with the same `session_id` route to the same model (with TICKET-8).
- Upstream still never receives `plugins`/`session_id`/`models`.
**Files likely affected:** `src/nimmakai/routes/openai.py`, `src/nimmakai/compat.py`, `tests/test_auto_router.py`

### TICKET-3: Advance the chain on transport errors; wire the circuit breaker to real outcomes
**Priority:** Critical
**Area:** routing
**Problem:** `FallbackExecutor` catches only `RuntimeError`, but `UpstreamClient` re-raises raw `httpx` exceptions after retries. A provider with DNS/TLS/socket problems fails the whole request (as a non-OpenAI 500) even with healthy fallbacks, and the circuit breaker never learns about HTTP or transport failures (and never closes: `succeed()` has no call sites).
**Fix:** Catch `(RuntimeError, httpx.HTTPError, OSError)` in `execute_json`/`execute_stream` attempt loops; record outcome + `circuit_breaker.fail(pid)` and advance. Call `circuit_breaker.succeed(pid)` on 2xx and `fail(pid)` on 5xx/transport in both paths.
**Acceptance criteria:**
- With model A's provider unreachable (connect refused), a 2-model chain serves from model B; response headers show `fallback_index=1`.
- After 5 consecutive transport failures, the provider's breaker is OPEN and `client_for_model` skips it (existing behavior); a later success closes it.
- No raw `httpx` exception escapes `_chat_like`.
**Files likely affected:** `src/nimmakai/routing/fallback.py`, `src/nimmakai/catalog/hub.py`, `tests/test_fallback.py`, `tests/test_hub.py`

### TICKET-4: OpenAI-shaped error envelope on every non-2xx path
**Priority:** Critical
**Area:** compat
**Problem:** Multiple paths emit non-OpenAI error shapes: auth errors are nested under `{"detail": ...}` (F-04); unhandled exceptions return Starlette's `{"detail":"Internal Server Error"}` (F-03/F-09); terminal upstream errors pass through provider-specific or string bodies (F-19/F-25); streamed 429/401 exhaustion returns empty bodies (F-06). OpenAI SDKs (Cursor/Cline) surface these as unparseable failures.
**Fix:** (1) Add app-level exception handlers: `HTTPException` → unwrap dict `detail` to top level; catch-all → 500 `{"error":{"message","type":"server_error","code":"internal_error"}}`. (2) In `FallbackExecutor`, normalize any terminal ≥400 body that isn't `{"error": {...}}` into the envelope (preserve raw under `error.metadata.raw`); synthesize a body for empty stream errors, including `Retry-After` for 429.
**Acceptance criteria:**
- `curl` without a key → top-level `{"error":{"code":"missing_api_key"}}`, status 401.
- Kill switch test: all-429 streaming request returns 429 with non-empty OpenAI error JSON.
- Upstream HTML error page → 502 with `{"error":{...}}`, never a JSON string body.
- `openai` Python SDK raises typed errors with populated `.message` for all of the above.
**Files likely affected:** `src/nimmakai/main.py`, `src/nimmakai/routes/openai.py`, `src/nimmakai/routing/fallback.py`, `src/nimmakai/upstream.py`

### TICKET-5: Never return 2xx for a stream that produced no bytes
**Priority:** Critical
**Area:** routing
**Problem:** When every chain model opens a stream but stalls past TTFT (or errors at open), the terminal return reuses the last 2xx status with an empty iterator — the client receives HTTP 200 and zero bytes. Cursor shows an eternal spinner; nothing is logged client-side.
**Fix:** Track whether any stream was successfully relayed; the terminal `StreamResult` must use 504 (TTFT exhaustion) or the last real error status, with an OpenAI error body (per TICKET-4's synthesizer).
**Acceptance criteria:**
- All-models-stall test (stub 200 + no bytes): response is 504 with `{"error":{"code":"upstream_timeout"}}`.
- Mixed test (model A stalls, model B streams): 200 with B's stream, `fallback_index=1`.
**Files likely affected:** `src/nimmakai/routing/fallback.py`, `tests/test_fallback.py`

### TICKET-6: Terminate broken streams with finish_reason + error event, not bare `[DONE]`
**Priority:** High
**Area:** compat
**Problem:** Mid-stream upstream failures yield `data: [DONE]` with no final chunk, so truncated output — including partial `tool_calls` JSON arguments — is presented as a complete response. An agent can execute a malformed tool call built from half-streamed arguments.
**Fix:** In `robust_iter` (and `_gated_stream`'s error path), before `[DONE]` emit a final SSE chunk with `finish_reason` (use `"error"`; `"length"` only if a length signal was seen) and a `data: {"error": {...}}` event describing the interruption.
**Acceptance criteria:**
- Simulated mid-stream disconnect during a tool-call delta: client receives an error event + finish chunk + `[DONE]`; the OpenAI SDK stream iterator raises/completes with an error rather than yielding a truncated tool call silently.
- Healthy streams are byte-identical to today.
**Files likely affected:** `src/nimmakai/routing/fallback.py`, `src/nimmakai/routes/openai.py`, `src/nimmakai/compat.py`

### TICKET-7: Eliminate the post-stream-open in_flight leak
**Priority:** Critical
**Area:** routing
**Problem:** After `execute_stream` returns an open stream, `routing_headers` → `hub.client_for_model` can raise (circuit opened concurrently). The exception path returns without consuming the stream iterator, so `httpx` response close and `pool.release` never run; the key's `in_flight` count leaks permanently. Three leaks brick a key until restart — a silent capacity death spiral under exactly the failure storms that open circuits.
**Fix:** Store the provider id resolved during `execute_stream` on `StreamResult`; make `routing_headers` use it (no `client_for_model` call, never raises). Wrap all post-open work in `try/except` that `aclose()`s `result.byte_iter` before re-raising.
**Acceptance criteria:**
- Fault-injection test: force `client_for_model` to raise after stream open → key's `in_flight` returns to 0; pool snapshot shows the key available.
- `routing_headers` has no raising call paths (unit test with open circuit).
**Files likely affected:** `src/nimmakai/routing/fallback.py`, `src/nimmakai/routes/openai.py`, `tests/test_fallback.py`

### TICKET-8: Honor pinned heads (sticky session + explicit model) in `_chain`
**Priority:** High
**Area:** routing
**Problem:** The selector carefully places the sticky-pinned or explicitly-requested model at the chain head, but `FallbackExecutor._chain` re-sorts the whole chain by live score, discarding the pin. Sticky sessions don't stick (multi-turn Cursor sessions hop models — losing upstream KV-cache locality and risking strict-provider 400s on foreign `tool_call_id` formats), and explicit model requests can be served by a different model with zero failures.
**Fix:** Add `pinned_head: str | None` to `RouteDecision` (set for `sticky_model`, explicit passthrough, and alias-to-model modes). In `_chain`, re-rank only the tail; keep the pinned head first unless `health.is_unhealthy(pinned)` — then demote it and log `pin_demoted`.
**Acceptance criteria:**
- Two sequential auto requests with the same session id route to the same model while it stays healthy (`X-Nimmakai-Model` stable).
- Explicit request for a healthy, live model is always served by that model (`fallback_index=0`).
- Pinned model in cooldown → served by next-best, and the pin updates to the new model on success (existing `put_both` behavior).
**Files likely affected:** `src/nimmakai/routing/selector.py`, `src/nimmakai/routing/fallback.py`, `src/nimmakai/routing/auto_router.py`, `tests/test_selector.py`

### TICKET-9: Correct concurrency-gate error shape, sizing, and leak-proofing
**Priority:** High
**Area:** compat
**Problem:** Gate exhaustion raises `RuntimeError` inside `_prepare_routed`, which re-raises → generic 500 instead of the intended 503 `nimmakai_pool_exhausted`. The gate is sized from the default provider's pool only, throttling multi-provider deployments. Any exception between gate acquire and `_prepare_routed` return leaks a slot forever.
**Fix:** Catch `RuntimeError` from `_prepare_routed` in `_chat_like`/`embeddings` and return `guard.pool_exhausted_error()` as 503 with `Retry-After`; size the gate as the sum of `len(pool) × max_in_flight_per_key` across active providers, recomputed on provider upsert/remove; wrap post-acquire logic in `try/except` that releases on failure.
**Acceptance criteria:**
- `global_max_in_flight=1` + 2 concurrent slow requests → second gets 503 `{"error":{"code":"nimmakai_pool_exhausted"}}`.
- Injected selector exception → gate `in_flight` returns to 0.
- Gate capacity reflects all active providers after enabling a second provider at runtime.
**Files likely affected:** `src/nimmakai/routes/openai.py`, `src/nimmakai/safety/guard.py`, `src/nimmakai/safety/concurrency.py`

### TICKET-10: End-to-end request deadline + fast connect + stream-aware inter-model backoff
**Priority:** High
**Area:** routing
**Problem:** `request_deadline_seconds` is configured but never enforced; worst-case latency is minutes (30s gate + per-model 30s pool waits + 30s connects × 3 + index-scaled inter-model backoffs up to 16s/hop). Behind Heroku's 30s router (this repo ships a `Procfile`), slow cascades die as connection resets with no error body.
**Fix:** Thread a monotonic deadline from request start through `_prepare_routed`, `pool.acquire(max_wait=remaining)`, and both executor loops; stop advancing when <8s remain and return 504 (OpenAI shape). Set `connect=5.0` in `UpstreamClient`. In `execute_stream`, skip inter-model backoff when the next candidate is on a different provider; cap same-provider backoff at 2s.
**Acceptance criteria:**
- With `request_deadline_seconds=20` and all models stalling, the client receives a 504 error body in ≤22s.
- Stream fallback across two providers inserts no sleep between hops (log-verified).
- Connect-refused provider consumes ≤~15s before chain advance (5s × 3, minus TICKET-3 making it 1 attempt-class failure).
**Files likely affected:** `src/nimmakai/routes/openai.py`, `src/nimmakai/routing/fallback.py`, `src/nimmakai/upstream.py`, `src/nimmakai/balancer.py`, `src/nimmakai/config.py`

### TICKET-11: Move blocking I/O and heavy CPU off the event loop
**Priority:** High
**Area:** caching
**Problem:** The request path performs synchronous disk/DB work on the asyncio loop: `learning.save()` JSON-dumps and writes the whole learning store inside `record_outcome`; `cost_overrides_map()` reads SQLite per request under the same `threading.Lock` the analytics writer holds across batch commits (loop can block behind an fsync); `recompute_rankings` runs full regex scoring on the loop. Each stall freezes every concurrent stream.
**Fix:** `learning.save_if_due` schedules `asyncio.to_thread(self.save)` (guard against concurrent saves with an atomic dirty flag); cost overrides become the §3 write-through in-memory cache; ladder rebuilds run in `asyncio.to_thread` with an atomic `_ladders` swap.
**Acceptance criteria:**
- No `write_text`/`sqlite3` calls on the event loop during a chat request (asserted via a loop-blocking watchdog test or `asyncio` debug slow-callback log = clean under load).
- Learning data still persists within 60s of last change; cost overrides reflect admin edits immediately.
**Files likely affected:** `src/nimmakai/catalog/learning.py`, `src/nimmakai/routes/openai.py`, `src/nimmakai/analytics/store.py`, `src/nimmakai/routes/admin.py`, `src/nimmakai/catalog/registry.py`

### TICKET-12: Classifier short-circuit + bounded text scan
**Priority:** Medium
**Area:** classification
**Problem:** `_extract_features` joins and lowercases the entire conversation and runs three regex/substring scans before `_rules` can short-circuit on `tools_present` — Cursor payloads reach 100KB–1MB, costing 10–30ms of event-loop CPU per request whose classification was already decided by the presence of `tools`.
**Fix:** Check `tools`/`tool_choice`/tool-role membership first and return `coding_agentic` before text feature extraction; when text analysis is needed, scan only the first and last 16KB of the joined text (fingerprints live in the system prompt head; recent intent lives at the tail). Tighten the `"cursor"` fingerprint to phrase-level matches.
**Acceptance criteria:**
- Classification of a 1MB tools-present body completes in <0.5ms (micro-benchmark).
- Rule outcomes on `tests/test_classifier.py` corpus unchanged except documented fingerprint tightening.
**Files likely affected:** `src/nimmakai/routing/classifier.py`, `tests/test_classifier.py`

### TICKET-13: Context-length-aware chain filtering
**Priority:** High
**Area:** routing
**Problem:** Known context lengths (`registry.context_by_model`) are never used for routing, so 100k+ char Cursor payloads are sent to small-context models, guaranteeing 400s recovered only by provider-message heuristics — seconds of wasted hops, or a user-facing 400 when the provider's wording isn't recognized.
**Fix:** Estimate request tokens (`char_length/3.5 + max_tokens buffer`) in `_prepare_routed` (stats already computed) and attach to `RouteDecision`; in `_chain`, drop models whose known context is below the estimate (retain unknown-context models). Keep the existing overflow-message fallback as a safety net.
**Acceptance criteria:**
- A 150k-char request never attempts a model with a known 16k context (verified via spans).
- Requests still route when no model's context is known.
**Files likely affected:** `src/nimmakai/routing/selector.py`, `src/nimmakai/routing/fallback.py`, `src/nimmakai/routes/openai.py`

### TICKET-14: Implement the §3 caching layer
**Priority:** High
**Area:** caching
**Problem:** No route-plan, catalog-payload, embeddings, or config caching exists; the chain is re-ranked 2–3× per request, `/v1/models` re-enriches every model on every poll, identical embedding inputs are re-billed upstream, and `prompt_cache_key` is stripped, hurting upstream prompt-cache hit rates.
**Fix:** Add a small `TTLCache` utility and wire the four caches from §3 (route-plan 1s; `/v1/models` bytes+ETag with event invalidation; embeddings sha256 LRU 24h/256MB; cost-overrides write-through). Make `_chain` the single ranking owner (selector stops calling `optimize_chain`). Forward `prompt_cache_key`.
**Acceptance criteria:**
- Two identical-intent requests within 1s perform one `optimize_chain` (counter/log).
- `/v1/models` with `If-None-Match` returns 304; payload changes after a provider is added.
- Repeated identical embeddings request is served from cache (no upstream span) and returns a byte-identical body.
- `prompt_cache_key` observed in upstream request bodies.
**Files likely affected:** new `src/nimmakai/cache.py`, `src/nimmakai/routes/openai.py`, `src/nimmakai/routing/fallback.py`, `src/nimmakai/routing/selector.py`, `src/nimmakai/compat.py`

### TICKET-15: Bound rankings-cache staleness and recompute on catalog delta
**Priority:** Medium
**Area:** invalidation
**Problem:** The frozen rankings cache is imported at boot with no age check and the hub refresh loop never recomputes, so chain order can be weeks old and newly-live models never join non-coding chains until a manual admin refresh or a >30% fallback-advance rate.
**Fix:** On `bind_db` import, if `computed_at` is older than 24h, schedule a background recompute (serve the stale cache meanwhile). In `refresh_from_hub` with `recompute_rankings=False`, detect live models absent from every frozen ladder and trigger a background recompute (the check exists in `_rebuild_all_chains`; call it from the hub path).
**Acceptance criteria:**
- Boot with a 25h-old cache → rankings recomputed within one refresh cycle without blocking startup.
- Adding a provider with new models updates `chat_fast`/`reasoning` ladders within one refresh cycle.
**Files likely affected:** `src/nimmakai/catalog/registry.py`, `src/nimmakai/main.py`, `tests/test_ranking_cache.py`

### TICKET-16: Convert JSON-downgraded "streams" to SSE
**Priority:** Medium
**Area:** compat
**Problem:** Some providers ignore `stream:true` (e.g. for forced tool_choice) and return a 2xx `application/json` body; the proxy relays it under a streaming response, and OpenAI SDK stream parsers fail or hang.
**Fix:** In `execute_stream`, when status is 2xx and content-type is JSON, read the body (bounded) and emit an equivalent single-chunk SSE sequence (role chunk, content/tool_calls chunk, usage chunk, `[DONE]`), preserving the routed-model rewrite.
**Acceptance criteria:**
- Stubbed JSON-on-stream upstream → client receives valid SSE; `openai` SDK stream iteration yields the full message and terminates cleanly.
- True SSE upstreams unaffected.
**Files likely affected:** `src/nimmakai/routing/fallback.py`, `src/nimmakai/compat.py`

### TICKET-17: `/v1/embeddings` parity — guarded parse, auth-first, size cap
**Priority:** Medium
**Area:** compat
**Problem:** The embeddings endpoint parses the body before authentication and without a try/except, so malformed JSON produces a `{"detail": ...}` 500, and unauthenticated clients can drive parsing of unbounded bodies (no global size limit exists).
**Fix:** Validate proxy auth from headers before body parse; wrap `request.json()` with the same 400 `invalid_json` error used in `_chat_like`; add a body-size limit middleware (default 20MB, configurable) applied to all `/v1/*` POSTs.
**Acceptance criteria:**
- Malformed JSON → 400 `{"error":{"code":"invalid_json"}}`.
- Missing key → 401 before any body read (verified with a streaming client that delays the body).
- >20MB body → 413 OpenAI-shaped error.
**Files likely affected:** `src/nimmakai/routes/openai.py`, `src/nimmakai/main.py`, `src/nimmakai/auth.py`

### TICKET-18: Stop silently mutating requests (`n`, `prompt_cache_key`)
**Priority:** Medium
**Area:** compat
**Problem:** `n>1` is silently forced to 1 (client receives fewer choices than requested with no signal), and `prompt_cache_key`/`user`/`store`/`metadata` are stripped globally — `prompt_cache_key` removal directly reduces upstream prompt-cache hits (latency + cost) on providers that support it.
**Fix:** Return 400 (OpenAI shape, `code:"n_not_supported"`) for `n>1`; forward `prompt_cache_key` and `user`; keep stripping only fields with observed provider rejections, documented per-key in `_STRIP_BODY_KEYS` comments.
**Acceptance criteria:**
- `n:2` → 400 with clear message (not 1 silent choice).
- `prompt_cache_key` present in upstream bodies; `cached_tokens` observed non-zero on a supporting provider in multi-turn tests.
**Files likely affected:** `src/nimmakai/compat.py`, `tests/test_compat.py`

### TICKET-19: Vision intent with no vision models must not route blind
**Priority:** Low
**Area:** routing
**Problem:** When no vision-capable model is live, the emergency fallback routes image-bearing requests to `sorted(live_ids)` — an alphabetical text-only model — producing hallucinated "descriptions" or provider 400s.
**Fix:** If `intent == vision` and the vision ladder is empty, return 400 `{"error":{"code":"no_vision_model","message":"No vision-capable model is currently available."}}`.
**Acceptance criteria:**
- Image request with zero vision models live → 400 with the above code; no upstream call recorded.
- Vision routing unchanged when vision models exist.
**Files likely affected:** `src/nimmakai/routing/selector.py`, `tests/test_selector.py`

---

*Every finding above traces to a specific file:line examined during Pass 1 and stress-tested logically during Pass 2; reproduction steps are included where behavior is asserted. Gaps in coverage are declared in Section 1.*
