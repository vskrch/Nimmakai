# Dynamic Family-Based Model Selection Design

**Date:** 2026-07-10  
**Status:** Approved  
**Parent:** `docs/design-intelligent-router.md`

## Goal

Stop hardcoding a single “best” coding/chat model id. At runtime, resolve families against NVIDIA’s live documentation + `/v1/models`, confirm with gentle probes, and pick by preference + live speed/health — without clogging or risking account bans.

## Preference policy (user-approved)

| Intent | Primary | Fallbacks (in order) |
|--------|---------|----------------------|
| Generic / chat / short text | **Latest Nemotron** chat LLM | GLM 5.2 → Step 3.7 → MiniMax M3 |
| Coding / agentic / tools | **Latest Qwen** coding LLM | GLM 5.2 → Step 3.7 → MiniMax M3 |

- “Latest” = highest semantic version among matching family ids present in live catalog.
- Exclude non-chat Nemotron (embed, OCR, ASR, safety, rerank) from text default.
- Exclude Qwen image-only models from coding primary.
- Live EWMA latency, success/error counts, and cooldowns may skip or demote an unhealthy head; they do not randomly invert the preference skeleton while the preferred family is healthy.

## Data sources

1. **B — Docs:** `https://build.nvidia.com/models.md` (+ `?page=N`), detail pages for `publisher` → `org/model` id.
2. **API:** `GET /v1/models`.
3. **C — Probes:** minimal chat (`max_tokens: 8`), budgeted; `200`/`429` = hosted; `404` = unavailable.
4. **Fail-safe:** disk snapshot of last good ranked chains; never invent dead hardcoded ids.

## Anti-ban

- Probe budget (default ~6–12/hour), sequential + jitter, no full-catalog stampede.
- Prefer learning from real request outcomes over probes.
- Existing KeyPool RPM/RPD/jitter/sticky/quarantine unchanged.

## Config shape

YAML holds **family preferences + scoring rules**, not concrete best-model ids.
