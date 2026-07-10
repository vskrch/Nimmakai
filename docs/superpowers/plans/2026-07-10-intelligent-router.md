# Intelligent Router Implementation Plan

> **For agentic workers:** Execute end-to-end from `docs/design-intelligent-router.md`.

**Goal:** Ship intent-aware model routing, ordered fallback, and account-safe multi-key operation on top of the bootstrap proxy.

**Architecture:** Extend `KeyPool`/`UpstreamClient`; add `catalog/`, `routing/`, `safety/`; wire through `routes/openai.py` behind `ROUTING_ENABLED`.

**Tech Stack:** FastAPI, httpx, pydantic-settings, PyYAML, pytest

---

## Tasks

- [x] Branch `feat/intelligent-router`
- [ ] Catalog schema + `config/models.yaml` + settings
- [ ] Live refresh + health store
- [ ] Safety: quarantine, RPD, jitter, sticky, concurrency, Retry-After
- [ ] Intent classifier (rules + optional LLM)
- [ ] Selector + fallback + route integration
- [ ] Admin/catalog endpoints + synthetic `nimmakai/auto`
- [ ] Optional egress proxies
- [ ] Tests + README + `.env.example`
- [ ] Verify pytest + ruff
