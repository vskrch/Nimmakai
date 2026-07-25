# Per-model context window (dynamic) — design

**Date:** 2026-07-10  
**Status:** approved — implement  
**Goal:** Each model exposes its **own maximum context window**, discovered dynamically at runtime from NVIDIA — never hardcoded in code, YAML, or a static DB. Clients (Cursor/agents) can size prompts correctly; Potato never under-clamps.

## Problem

Agents size prompts using `context_length` (and cousins) from `GET /v1/models`. Potato currently:

- Proxies `GET /models` but does not enrich or persist per-model windows
- Passes `max_tokens` / sampling through unchanged (correct for passthrough) but does not advertise real windows
- Uses prompt **character count** only for intent (`long_horizon`), not capacity

That can cause agentic errors when the client assumes a wrong window.

## Clarifications (unbiased review)

1. **`max_tokens` ≠ context window.** `max_tokens` is the *completion* budget. “Use maximum context” means **advertise the model’s input+output capacity** and **never lower** client `max_tokens`. It does **not** mean set `max_tokens = context_length` (that would waste/break requests).
2. **NVIDIA often omits context on `/v1/models`.** Many NIM catalog entries only have `id` / `object` / `owned_by`. Discovery must tolerate missing fields and fall back to docs text; if still unknown → **omit** the field (never invent).
3. **`potato/auto` has no single window.** Omit `context_length` on the synthetic auto entry (do not pick an arbitrary max — that lies to agents).
4. **Skip-before-send vs retry-on-error.** User rejected preemptive “prompt too big → weaker model.” Spec still allows treating a clear upstream **context exceeded** error as **retryable ladder advance** (same class as model unavailable) so one oversized turn can try the next stronger-available model without rewriting history.
5. **Today’s `/v1/models` path** always hits upstream live; enrichment must merge discovered values onto each item (and onto cached registry state used when upstream is partial).

## Non-goals

- Prompt trimming / history rewriting
- Preemptive skip because estimated tokens > window
- Hardcoded context tables in code, YAML, or curated DBs
- Clamping client `max_tokens` **down**
- Per-model default `temperature` / `top_p` (separate work)

## Approach

**Dynamic discovery → store in live catalog → advertise on `/v1/models` → never under-clamp → optional retry on context-exceeded.**

### Discovery (each catalog refresh)

From upstream `GET /models` `data[]` items, extract the first positive int among (order fixed, not a “table of models”):

- top-level: `context_length`, `max_model_len`, `max_sequence_length`, `context_window`
- nested: `meta.*`, `parameters.*`, `model_info.*` same keys
- ignore `max_tokens` on the **model object** unless clearly named as context (prefer explicit context_* keys; `max_tokens` on model cards is ambiguous)

From docs detail / description text when API lacks a value: parse patterns like `128K context`, `context length: 131072`, `up to 1M tokens` (conservative regex; discard absurd values outside e.g. 1_024 … 10_000_000).

Persist `context_by_model: {model_id: int}` in memory + optional `.potato/catalog_snapshot.json` as **last-fetch cache only**.

### Advertise

On Potato `GET /v1/models` and `GET /v1/models/{id}`:

- For each real model: if we know a window, set `context_length` (and mirror to `max_model_len` if absent) so common clients see it.
- Prefer **max(upstream_reported, discovered)** when both exist and differ (never shrink a larger upstream value).
- `potato/auto`: no `context_length`.

### Request path

- Chat/completions/etc.: passthrough; only rewrite `model`.
- Do not inject or reduce `max_tokens`.
- Response diagnostic (optional): `X-Potato-Context-Length: <n>` when known for the model actually used.

### Fallback

- If upstream error body/message clearly indicates context overflow, treat as retryable and advance ladder (like model-not-found), without trimming messages.

## Upstream alignment

| Client | Upstream (`NIM_BASE_URL`) |
|--------|---------------------------|
| `GET /v1/models` | `GET /models` (+ enrich) |
| `GET /v1/models/{id}` | `GET /models/{id}` (+ enrich) |
| `POST /v1/chat/completions` | `POST /chat/completions` |
| `POST /v1/completions` | `POST /completions` |
| `POST /v1/embeddings` | `POST /embeddings` |
| `POST /v1/responses` | `POST /responses` |

## Success criteria

1. After refresh, models with discoverable windows show `context_length` on our `/v1/models`.
2. No hardcoded per-model sizes in repo.
3. Client `max_tokens` never lowered by Potato.
4. Unknown → field omitted.
5. Clear context-exceeded upstream errors advance the ladder.
6. Unit tests for extractors, merge/enrich, retryable context error.

## Remaining risks (accepted)

- Docs regex can miss or mis-parse marketing copy → omit rather than guess wrong small.
- Some NVIDIA models never publish window → clients still may mis-size; we cannot invent.
- Token estimate ≠ chars; without preemptive skip, overflow can still happen once before fallback advance.

## Spec self-review

- Ambiguity on `max_tokens` vs context clarified
- `potato/auto` decided (omit)
- Retry-on-context-error included without contradicting “no preemptive skip”
- No hardcoded model→size map
