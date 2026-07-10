# Dynamic context window — Implementation Plan

> **For agentic workers:** Execute task-by-task. Steps use checkbox syntax.

**Goal:** Discover each NIM model’s max context window at runtime and advertise it on `/v1/models` without hardcoding or under-clamping `max_tokens`.

**Architecture:** Extract context ints from NVIDIA `/models` payloads + docs text into `registry.context_by_model`; merge into list/get model responses; treat clear context-exceeded errors as ladder-retryable; optional response header.

**Tech Stack:** Python 3.11, existing ModelRegistry / FallbackExecutor / FastAPI routes.

---

### Task 1: Context extractor + tests
- [ ] Add `src/nimmakai/catalog/context.py` with `extract_context_length(obj)`, `parse_context_from_text(text)`, `merge_context(existing, new)`
- [ ] Tests in `tests/test_context.py`

### Task 2: Registry storage + refresh
- [ ] `context_by_model` on ModelRegistry; fill during `refresh_from_upstream`; snapshot load/save
- [ ] `enrich_model_dict(item)` helper; `context_length_for(model_id)`

### Task 3: Advertise on routes
- [ ] Enrich `GET /v1/models` and `GET /v1/models/{id}`
- [ ] `synthetic_auto_model` omits context_length
- [ ] `X-Nimmakai-Context-Length` on routed chat responses when known

### Task 4: Fallback retry on context exceeded
- [ ] Extend `_is_retryable_model_error` for context overflow phrases

### Task 5: Verify
- [ ] `pytest` + `ruff`
