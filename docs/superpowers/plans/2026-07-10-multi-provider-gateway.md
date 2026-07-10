# Multi-provider gateway Phase 1 — Implementation Plan

> **For agentic workers:** Execute task-by-task.

**Goal:** Register OpenAI-compatible providers; merge models; route like a self-hosted OpenRouter while keeping NIM working.

**Architecture:** `ProviderRegistry` owns provider configs + per-provider `KeyPool`/`UpstreamClient`; model ids are `provider_id/...`; `ModelRegistry` + ladder operate on namespaced ids; fallback picks upstream by prefix.

**Tech Stack:** Existing FastAPI / httpx / YAML.

---

### Task 1: Provider schema + load/save
- [ ] `catalog/providers.py` — ProviderConfig, load YAML, mask keys, persist overlay
- [ ] `config/providers.yaml` with built-in `nim` placeholder
- [ ] Tests

### Task 2: Multi-upstream wiring
- [ ] `ProviderHub` creates pools/clients per provider
- [ ] main.py lifespan uses hub; nim from env still works

### Task 3: Namespaced catalog + refresh
- [ ] Refresh all providers; store `provider_id/model`
- [ ] context extract on namespaced ids

### Task 4: Routing resolve
- [ ] Strip provider prefix for upstream body `model`
- [ ] Fallback uses hub.client_for(model)

### Task 5: Admin API
- [ ] CRUD + refresh endpoints
- [ ] Tests

### Task 6: Docs + pytest
