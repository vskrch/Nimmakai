# Nimmakai — Dynamic Intelligence Router: Production ADR

> **Status**: Draft — Ready for Agentic Execution  
> **Date**: 2026-07-24  
> **Scope**: Zero-hardcode routing engine, 504 fix, internet-primary model scoring, production hardening  
> **Goal**: Low-latency, highly-available LLM API gateway with fully adaptive, self-improving routing intelligence

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Architecture Decision](#2-architecture-decision)
3. [Current State Gap Analysis](#3-current-state-gap-analysis)
4. [Epic 1 — Config Externalization (NMK-C1xx)](#epic-1--config-externalization)
5. [Epic 2 — Fix 504 Cascade (NMK-R2xx)](#epic-2--fix-504-cascade)
6. [Epic 3 — Intel Fetcher: Internet-Primary Scoring (NMK-I3xx)](#epic-3--intel-fetcher)
7. [Epic 4 — ModelScoreCache: Dynamic Intelligence Engine (NMK-S4xx)](#epic-4--model-score-cache)
8. [Epic 5 — LadderService Hardcode Removal (NMK-L5xx)](#epic-5--ladder-hardcode-removal)
9. [Epic 6 — Routing Layer Hardcode Removal (NMK-RT6xx)](#epic-6--routing-hardcode-removal)
10. [Epic 7 — Health & Cooldown (NMK-H7xx)](#epic-7--health--cooldown)
11. [Epic 8 — Registry & Lifecycle (NMK-G8xx)](#epic-8--registry--lifecycle)
12. [Epic 9 — Production Hardening (NMK-P9xx)](#epic-9--production-hardening)
13. [Ticket Dependency Graph](#ticket-dependency-graph)
14. [Verification Matrix](#verification-matrix)

---

## 1. Problem Statement

### 1.1 Immediate Production Issues

| Issue | Symptom | Root Cause |
|-------|---------|------------|
| **504 cascade** | All upstream providers returning 504 | `request_deadline_seconds=10s` conflicts with `per-attempt budget=45s`; no 504-specific advance logic; 504 sleeps like 503 |
| **Hardcoded quality tiers** | `QUALITY_TIERS` regex table (60+ patterns) in `ladder.py` goes stale as new models ship | Static Python list; no internet refresh |
| **Coding-only special branches** | `coding_candidates()` expands coding chains to 12 models inflating timeout chain | 7 files have `if intent == "coding_agentic"` logic instead of data-driven routing |
| **Provider speed priors static** | `PROVIDER_SPEED_PRIOR` dict in `presets.py` hardcoded; never reflects actual TPS | No feedback loop from health tracker to routing weights |
| **No 504 model cooldown** | A 504-ing model stays top-of-chain for next request | `health.py` has no 504 case in `record_outcome()` |
| **Reasoning tasks get no elite boost** | Only coding has `_coding_elite_boost()`; reasoning models get no bonus on reasoning tasks | Intent-specific boost logic missing for non-coding intents |

### 1.2 Architectural Debt

The current system encodes routing intelligence **in Python code** (regex patterns, if/else branches, hardcoded scalars). This means every new model family requires a code deploy, no adaptation to real observed outcomes, intelligence degrades as LLM landscape evolves, and quality is inconsistent across intents.

### 1.3 Target State

A fully adaptive routing engine where model intelligence scores are computed from live internet sources (benchmarks, leaderboard ELOs, measured TPS), cached atomically, refreshed periodically, and corrected continuously by Thompson Sampling from real routing outcomes. The YAML config is a robust fallback, not the primary source of truth.

---

## 2. Architecture Decision

### 2.1 System Architecture

```
CLIENT REQUEST
     │
     ▼
IntentClassifier  (unchanged — already good)
→ intent: coding_agentic | reasoning | chat_fast | long_horizon | vision | embeddings
     │
     ▼
ModelSelector.resolve()  (hardcode-free)
→ registry.chain_for_intent(intent)
  ↑ LadderService (precomputed frozen ladder)
     ↑ ModelScoreCache (atomic reference, O(1) read)
     │
     ▼
FallbackExecutor  (504-aware, intent-budgeted)
→ optimize_chain(chain, intent)  [INTENT_WEIGHTS from yaml]
→ per-model attempt: 504→immediate advance, 503→tiny backoff
→ record_outcome() → LearningStore + Health (504 cooldown)
     │
     ▼
UPSTREAM PROVIDER
```

**BACKGROUND (async, periodic 300s):**
```
IntelFetcher
  ├── OpenRouter /api/v1/models       (capabilities, context — no key needed)
  ├── ArtificialAnalysis API          (intelligence_index, TPS — optional key)
  ├── HuggingFace OpenEvals parquet   (MMLU, HumanEval normalized — no key)
  └── Arena-AI community JSON         (ELO scores — no key)
→ IntelBundle dict  (disk-cached .nimmakai/intel_cache.json)

ModelScoreCache.recompute(live_ids, intel_bundles, health, learning, yaml_cfg)
→ atomic install → LadderService.rebuild()
```

### 2.2 Data Priority Waterfall

```
Quality Score for model M:
  1st: ArtificialAnalysis intelligence_index   (weight 0.40) — real benchmark composite
  2nd: HuggingFace OpenEvals MMLU+HumanEval    (weight 0.30) — normalized 0-100
  3rd: Arena community ELO                     (weight 0.20) — (ELO-800)/8 clamped 0-100
  4th: Param count log-scale estimate          (weight 0.10) — 7B→60, 400B→92
  5th: YAML quality_floor_keywords             (fallback)    — static default

Speed Score for model M:
  1st: Health tracker EWMA TPS                 — measured real outcomes
  2nd: ArtificialAnalysis output_speed         — external measurement
  3rd: YAML provider_speed_prior               — cold-start default

Capability flags for model M:
  1st: OpenRouter /api/v1/models               — supported_parameters field
  2nd: Each provider /v1/models                — modalities/capabilities field
  3rd: YAML capability_hints patterns          — fallback

Intent affinity for (model M, intent I):
  base: capability match delta (tools→coding_agentic, vision→vision, etc.) from yaml
  × Thompson posterior  (alpha/(alpha+beta) from real outcomes, intent-specific)
  × quality tier factor (frontier quality>=90 gets boost on hard intents)
```

---

## 3. Current State Gap Analysis

### Hardcoded Locations — Complete Inventory

| File | Approx Line | What | Fix Ticket |
|------|-------------|------|-----------|
| `catalog/ladder.py` | 44–154 | `QUALITY_TIERS` 60+ regex→score | NMK-L501 |
| `catalog/ladder.py` | 182–270 | `INTENT_AFFINITY` static dict | NMK-L502 |
| `catalog/ladder.py` | 279–289 | `INTENT_KEYWORDS` dict | NMK-L503 |
| `catalog/ladder.py` | 273 | `_MAX_HEAD_FAMILY_STREAK = 2` | NMK-C101 |
| `catalog/ladder.py` | 276 | `_DEFAULT_AFFINITY = 0.85` | NMK-C101 |
| `catalog/ladder.py` | 300 | `UCB_C = 5.0` | NMK-C101 |
| `catalog/ladder.py` | 608–630 | coding_agentic provider-prior branch | NMK-L504 |
| `catalog/ladder.py` | 619–638 | `_coding_elite_boost()` call site | NMK-L505 |
| `catalog/ladder.py` | 806–838 | `_coding_elite_boost()` method | NMK-L505 |
| `catalog/ladder.py` | 628 | `speed_blend = 0.62 + 0.38 * ...` | NMK-L504 |
| `catalog/ladder.py` | 872 | `(mean - 0.5) * 16.0` Thompson scale | NMK-C101 |
| `catalog/ladder.py` | 875 | `weight = min(1.0, model_n / 12.0)` | NMK-C101 |
| `catalog/health.py` | 44 | `error_rate_threshold = 0.45` | NMK-C102 |
| `catalog/health.py` | 47 | `model_cooldown_seconds = 45.0` | NMK-C102 |
| `catalog/health.py` | 48 | `hard_fail_cooldown_seconds = 5.0` | NMK-C102 |
| `catalog/health.py` | 109 | `now + 15.0` (429 cooldown) | NMK-C102 |
| `catalog/health.py` | 78 | `min(180.0, ...)` (max cooldown cap) | NMK-C102 |
| `catalog/health.py` | 174 | `window = min(8, len(healthy))` | NMK-C102 |
| `catalog/health.py` | 191 | `(now - last_success_at) < 30.0` | NMK-C102 |
| `catalog/health.py` | — | MISSING: 504 cooldown case | NMK-H701 |
| `catalog/presets.py` | 252–268 | `PROVIDER_SPEED_PRIOR` static dict | NMK-G801 |
| `catalog/presets.py` | 299–314 | `ZEN_FREE_CODING_MODELS` list | NMK-G802 |
| `catalog/registry.py` | 80–82 | `_coding_candidates_cache` | NMK-G803 |
| `catalog/registry.py` | 511–542 | `coding_candidates()` method | NMK-G803 |
| `routing/optimizer.py` | 31–34 | `_ALPHA_INTEL`, `_BETA_SPEED` scalars | NMK-RT601 |
| `routing/selector.py` | 267–272 | `coding_candidates()` expansion #1 | NMK-RT602 |
| `routing/selector.py` | 342–351 | `coding_candidates()` expansion #2 | NMK-RT602 |
| `routing/selector.py` | 396–400 | `coding_max_fallbacks` in auto resolve | NMK-RT603 |
| `routing/selector.py` | 491–496 | `coding_candidates()` expansion #3 | NMK-RT602 |
| `routing/fallback.py` | 470, 1183, 1197, 2067, 2081 | `coding_max_fallbacks` refs (5x) | NMK-RT604 |
| `routing/fallback.py` | 1466 | `min(remaining, 45.0)` hardcoded budget | NMK-R201 |
| `routing/fallback.py` | 1426 | `remaining < 3.0` deadline guard | NMK-R202 |
| `routing/auto_router.py` | 384–388 | `coding_candidates()` pool expansion | NMK-RT605 |
| `config.py` | 63 | `coding_max_fallbacks: int = 5` | NMK-C103 |
| `config.py` | ~100 | `request_deadline_seconds = 10.0` | NMK-C104 |
| `config.py` | ~102 | `upstream_timeout = 10.0` | NMK-C104 |

---

## Epic 1 — Config Externalization

**Goal**: Every magic number or scalar in routing code becomes a named config field. Zero behavior change — same defaults, just now configurable.

---

### NMK-C101: Externalize `ladder.py` Constants

**Files**: `src/nimmakai/config.py`, `src/nimmakai/catalog/ladder.py`

Add to `config.py`:
```python
ucb_exploration_c: float = 5.0
diversity_streak_max: int = 2
default_affinity: float = 0.85
thompson_scale: float = 16.0
thompson_blend_n: int = 12
health_window_size: int = 8
```

In `ladder.py`, pass these into `LadderService.__init__()` and replace all 6 literals with `self._ucb_c`, `self._diversity_streak`, `self._default_affinity`, etc. Delete `UCB_C`, `_MAX_HEAD_FAMILY_STREAK`, `_DEFAULT_AFFINITY` constants.

**Acceptance**: `python -m pytest tests/ -x -q` passes; `LadderService` accepts all tuning kwargs.

---

### NMK-C102: Externalize `health.py` Constants

**Files**: `src/nimmakai/config.py`, `src/nimmakai/catalog/health.py`

Add to `config.py`:
```python
error_rate_threshold: float = 0.45
model_cooldown_seconds: float = 45.0
hard_fail_cooldown_seconds: float = 5.0
max_cooldown_seconds: float = 180.0
rate_limit_cooldown_seconds: float = 15.0
gateway_timeout_cooldown_seconds: float = 30.0
health_window_size: int = 8
recent_success_window_seconds: float = 30.0
```

Convert `ModelHealthStore` dataclass fields to accept all above. Replace every inline literal. Wire in `main.py` during construction.

**Acceptance**: `ModelHealthStore(error_rate_threshold=0.6)` changes behavior as expected.

---

### NMK-C103: Remove `coding_max_fallbacks`, Add Universal + Per-Intent Budget

**File**: `src/nimmakai/config.py`

```python
# DELETE:
# coding_max_fallbacks: int = 5

# ADD:
max_model_fallbacks: int = 10
per_attempt_budget_seconds: float = 30.0
intent_max_fallbacks: dict = field(default_factory=lambda: {
    "coding_agentic": 10, "reasoning": 10, "long_horizon": 8,
    "chat_fast": 6, "vision": 6, "embeddings": 4,
})
intent_attempt_budget_seconds: dict = field(default_factory=lambda: {
    "coding_agentic": 30.0, "reasoning": 45.0, "long_horizon": 45.0,
    "chat_fast": 15.0, "vision": 20.0, "embeddings": 10.0,
})
```

**Acceptance**: `grep -r "coding_max_fallbacks" src/` returns zero hits after NMK-RT604.

---

### NMK-C104: Fix Request Deadline and Upstream Timeout (Root Cause of 504 Cascade)

**File**: `src/nimmakai/config.py`

```python
# BEFORE:
upstream_timeout: float = 10.0
request_deadline_seconds: float = 10.0
stream_ttft_timeout_seconds: float = 2.0
stream_idle_timeout_seconds: float = 30.0

# AFTER:
upstream_timeout: float = 120.0
request_deadline_seconds: float = 120.0
stream_ttft_timeout_seconds: float = 15.0
stream_idle_timeout_seconds: float = 60.0
```

Add code comment:
```python
# INVARIANT: upstream_timeout >= per_attempt_budget_seconds
# INVARIANT: request_deadline_seconds >= upstream_timeout
# Violating these guarantees a 504 cascade
```

**Acceptance**: 90-second streaming response completes without deadline error.

---

### NMK-C105: YAML `scoring` Section

**File**: `config/models.yaml` (or `src/nimmakai/data/models.yaml`)

Add complete scoring block:
```yaml
scoring:
  quality_signal_weights:
    aa_intelligence:  0.40
    hf_leaderboard:   0.30
    arena_elo:        0.20
    param_estimate:   0.10

  quality_bounds: {min: 10.0, max: 100.0}
  arena_elo_base: 800
  arena_elo_scale: 8.0

  intent_optimizer_weights:
    coding_agentic:  {intel: 0.50, speed: 0.32, avail: 0.15, prov: 0.03}
    reasoning:       {intel: 0.55, speed: 0.25, avail: 0.17, prov: 0.03}
    long_horizon:    {intel: 0.50, speed: 0.28, avail: 0.19, prov: 0.03}
    chat_fast:       {intel: 0.30, speed: 0.47, avail: 0.20, prov: 0.03}
    vision:          {intel: 0.45, speed: 0.35, avail: 0.17, prov: 0.03}
    embeddings:      {intel: 0.25, speed: 0.45, avail: 0.27, prov: 0.03}
    _default:        {intel: 0.45, speed: 0.35, avail: 0.17, prov: 0.03}

  intent_max_fallbacks:
    coding_agentic: 10
    reasoning:      10
    long_horizon:   8
    chat_fast:      6
    vision:         6
    embeddings:     4

  intent_attempt_budget_seconds:
    coding_agentic: 30.0
    reasoning:      45.0
    long_horizon:   45.0
    chat_fast:      15.0
    vision:         20.0
    embeddings:     10.0

  provider_speed_prior:
    groq: 1.35, cerebras: 1.40, sambanova: 1.30, zen: 1.28
    deepseek: 1.25, fireworks: 1.20, together: 1.15
    deepinfra: 1.12, hyperbolic: 1.12, mistral: 1.10
    gemini: 1.08, openrouter: 1.05, nim: 1.05
    github: 1.00, cloudflare: 1.00, _default: 1.00

  capability_affinity_deltas:
    tools_confirmed_true:   {coding_agentic: 0.25, reasoning: 0.15}
    tools_confirmed_false:  {coding_agentic: -0.80}
    vision_confirmed_true:  {vision: 0.30}
    vision_confirmed_false: {vision: -0.95}
    reasoning_confirmed_true: {reasoning: 0.30, coding_agentic: 0.10}
    long_context:           {long_horizon: 0.25}
    short_context:          {long_horizon: -0.30}
    small_param:            {chat_fast: 0.20}
    frontier_param:         {coding_agentic: 0.20, reasoning: 0.30}
    embed_confirmed_true:   {embeddings: 0.30}
    embed_confirmed_false:  {embeddings: -0.95}

  quality_floor_keywords:
    ultra: 88.0, super: 84.0, pro: 82.0, large: 80.0
    medium: 72.0, small: 68.0, mini: 65.0, micro: 60.0
    "70b": 78.0, "72b": 78.0, "400b": 92.0, "8b": 64.0, "7b": 62.0
    instruct: 65.0, _default: 60.0

  capability_hints:
    tools_true_patterns:    ["*gpt*","*claude*","*gemini*","*deepseek*","*qwen*","*mimo*"]
    vision_true_patterns:   ["*vision*","*vl*","*omni*","*gemini*","*gpt-4o*"]
    reasoning_true_patterns: ["*r1*","*o1*","*o3*","*o4*","*thinking*","*deepseek-r1*"]
    embed_true_patterns:    ["*embed*","*rerank*","*retrieval*"]
    exclude_chat_patterns:  ["*embed*","*rerank*","*ocr*","*asr*","*tts*","*diffusion*","*guard*","*safety*"]
```

**Acceptance**: `ModelScoreCache` loads all sections; missing sections fall back to `config.py` defaults.

---

## Epic 2 — Fix 504 Cascade

**Goal**: 504 from upstream triggers immediate chain advance with no backoff sleep. 504-ed models get a health cooldown.

---

### NMK-R201: Fix Per-Attempt Budget (Intent-Aware)

**File**: `src/nimmakai/routing/fallback.py`

**Problem**: `attempt_budget = max(1.0, min(remaining, 45.0))` — 45s hardcoded, ignores intent type.

**Fix** (apply in both `execute_json` ~line 1195 and `execute_stream` ~line 1466):
```python
# BEFORE:
attempt_budget = max(1.0, min(remaining, 45.0))

# AFTER:
_intent_budgets = getattr(self.settings, "intent_attempt_budget_seconds", {})
_default_budget = float(getattr(self.settings, "per_attempt_budget_seconds", 30.0))
_per_attempt = float(_intent_budgets.get(decision.intent.value, _default_budget))
attempt_budget = max(1.0, min(remaining, _per_attempt))
```

**Acceptance**: Reasoning requests get 45s; chat_fast gets 15s per attempt.

---

### NMK-R202: 504 Immediate Advance — No Backoff Sleep

**File**: `src/nimmakai/routing/fallback.py`

In both `execute_json` and `execute_stream` failure handling blocks:

```python
# AFTER (replace the flat status-check block):
if status == 429:
    retry_after = _parse_retry_after(headers)
    await asyncio.sleep(min(retry_after, 30.0))
    continue
elif status == 504:
    # Already timed out upstream — advance immediately, no sleep
    self.registry.record_outcome(
        model, key_id, success=False, status_code=504,
        intent=decision.intent.value
    )
    self.stats.fallback_advances += 1
    continue
elif status in {500, 502, 503}:
    await sleep_backoff(idx,
        base=getattr(self.settings, "retry_backoff_base_seconds", 0.2),
        cap=getattr(self.settings, "retry_backoff_cap_seconds", 2.0),
    )
    continue
```

**Acceptance**: When upstream returns 504, next model attempted within 200ms (no sleep delay).

---

### NMK-R203: Fix Deadline Consistency Between `execute_json` and `execute_stream`

**File**: `src/nimmakai/routing/fallback.py`

Extract shared helper so both paths use the same deadline:
```python
def _make_deadline(self) -> float:
    base = float(getattr(self.settings, "request_deadline_seconds", 120.0) or 120.0)
    return time.monotonic() + base
```

Replace `deadline = time.monotonic() + ...` in both execute methods with `self._make_deadline()`.

---

### NMK-R204: Externalize Deadline Guard Seconds

**File**: `src/nimmakai/config.py`

```python
deadline_guard_seconds: float = 3.0
retry_backoff_base_seconds: float = 0.2
retry_backoff_cap_seconds: float = 2.0
```

In `fallback.py` replace `remaining < 3.0` with `remaining < getattr(self.settings, "deadline_guard_seconds", 3.0)`.

---

## Epic 3 — Intel Fetcher

**Goal**: Async multi-source fetcher for model intelligence data. Internet sources are primary. Disk-cached for restart resilience.  
**File**: NEW `src/nimmakai/catalog/intel_fetcher.py`

---

### NMK-I301: `IntelBundle` Data Model

```python
@dataclass
class IntelBundle:
    """All externally-fetched intelligence data for one model (by bare slug)."""
    model_slug: str            # normalized bare name, no provider prefix

    # Quality signals (None = this source did not report)
    aa_intelligence_idx: float | None = None
    hf_mmlu: float | None = None
    hf_humaneval: float | None = None
    arena_elo: float | None = None
    param_b: float | None = None
    context_length: int | None = None
    aa_tps: float | None = None          # tokens/second from ArtificialAnalysis

    # Capability flags (None = unknown, True/False = confirmed)
    supports_tools: bool | None = None
    supports_vision: bool | None = None
    supports_reasoning: bool | None = None
    supports_embeddings: bool | None = None

    sources: list[str] = field(default_factory=list)
    fetched_at: float = 0.0

    def merge_from(self, other: IntelBundle) -> None:
        """Fill gaps from other. Never overwrites non-None values."""
        for f in fields(self):
            if f.name in ("model_slug", "sources", "fetched_at"):
                continue
            if getattr(self, f.name) is None:
                v = getattr(other, f.name)
                if v is not None:
                    object.__setattr__(self, f.name, v)
        for s in other.sources:
            if s not in self.sources:
                self.sources.append(s)
```

---

### NMK-I302: OpenRouter Source (No API Key)

```python
async def _fetch_openrouter(client: httpx.AsyncClient) -> dict[str, IntelBundle]:
    """
    Public endpoint — no API key needed.
    https://openrouter.ai/api/v1/models
    Returns: context_length, supported_parameters, input_modalities.
    """
    try:
        resp = await client.get(
            "https://openrouter.ai/api/v1/models",
            headers={"HTTP-Referer": "https://nimmakai.ai", "X-Title": "Nimmakai Router"},
            timeout=20.0,
        )
        resp.raise_for_status()
        models = resp.json().get("data") or []
    except Exception as exc:
        logger.warning("openrouter intel fetch failed: %s", exc)
        return {}

    bundles: dict[str, IntelBundle] = {}
    for m in models:
        slug = _normalize_slug(str(m.get("id") or ""))
        if not slug:
            continue
        sp = set(m.get("supported_parameters") or [])
        arch = m.get("architecture") or {}
        input_mods = set(arch.get("input_modalities") or [])
        output_mods = set(arch.get("output_modalities") or [])
        desc = str(m.get("description") or "").lower()
        bundles[slug] = IntelBundle(
            model_slug=slug,
            context_length=_safe_int(m.get("context_length")),
            supports_tools="tools" in sp,
            supports_vision="image" in input_mods,
            supports_reasoning=(
                "reasoning" in sp or "thinking" in sp
                or "reasoning" in desc or "chain-of-thought" in desc
            ),
            supports_embeddings="embeddings" in output_mods,
            sources=["openrouter"],
            fetched_at=time.time(),
        )
    logger.info("openrouter: %d model bundles fetched", len(bundles))
    return bundles
```

---

### NMK-I303: ArtificialAnalysis Source (Optional Key)

```python
async def _fetch_artificial_analysis(
    client: httpx.AsyncClient, api_key: str | None
) -> dict[str, IntelBundle]:
    """
    https://api.artificialanalysis.ai/v1/models
    Requires ARTIFICIAL_ANALYSIS_API_KEY. Graceful no-op if absent.
    Returns: intelligence_index (0-100), output_speed (TPS).
    """
    if not api_key:
        logger.debug("ARTIFICIAL_ANALYSIS_API_KEY not set; skipping source")
        return {}
    try:
        resp = await client.get(
            "https://api.artificialanalysis.ai/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=25.0,
        )
        resp.raise_for_status()
        data = resp.json()
        models = data.get("models") or (data if isinstance(data, list) else [])
    except Exception as exc:
        logger.warning("artificialanalysis intel fetch failed: %s", exc)
        return {}

    bundles: dict[str, IntelBundle] = {}
    for m in models:
        slug = _normalize_slug(str(m.get("model_id") or m.get("id") or m.get("name") or ""))
        if not slug:
            continue
        bundles[slug] = IntelBundle(
            model_slug=slug,
            aa_intelligence_idx=_safe_float(m.get("intelligence_index") or m.get("quality_index")),
            aa_tps=_safe_float(m.get("output_speed") or m.get("tokens_per_second")),
            sources=["artificialanalysis"],
            fetched_at=time.time(),
        )
    logger.info("artificialanalysis: %d model bundles fetched", len(bundles))
    return bundles
```

---

### NMK-I304: HuggingFace OpenEvals Source (No Auth)

```python
async def _fetch_hf_openeval(client: httpx.AsyncClient) -> dict[str, IntelBundle]:
    """
    Public HuggingFace OpenEvals parquet.
    https://huggingface.co/datasets/OpenEvals/leaderboard-data/...
    Uses pyarrow (optional dep). Graceful no-op if missing.
    """
    url = (
        "https://huggingface.co/datasets/OpenEvals/leaderboard-data"
        "/resolve/main/data/train-00000-of-00001.parquet"
    )
    try:
        raw = await asyncio.to_thread(_download_bytes_sync, url, 30.0)
    except Exception as exc:
        logger.warning("hf openeval fetch failed: %s", exc)
        return {}
    try:
        import io
        import pyarrow.parquet as pq
        table = await asyncio.to_thread(lambda: pq.read_table(io.BytesIO(raw)))
        rows = table.to_pylist()
    except ImportError:
        logger.info("pyarrow not installed; skipping HF openeval source")
        return {}
    except Exception as exc:
        logger.warning("hf openeval parse failed: %s", exc)
        return {}

    bundles: dict[str, IntelBundle] = {}
    for row in rows:
        slug = _normalize_slug(str(row.get("model") or row.get("model_name") or ""))
        if not slug:
            continue
        bundles[slug] = IntelBundle(
            model_slug=slug,
            hf_mmlu=_safe_float(row.get("mmlu") or row.get("mmlu_score")),
            hf_humaneval=_safe_float(row.get("humaneval") or row.get("humaneval_score")),
            sources=["hf_openeval"],
            fetched_at=time.time(),
        )
    logger.info("hf_openeval: %d model bundles fetched", len(bundles))
    return bundles
```

---

### NMK-I305: Arena Community Leaderboard Source (No Auth)

```python
async def _fetch_arena_leaderboard(client: httpx.AsyncClient) -> dict[str, IntelBundle]:
    """
    Community arena leaderboard: https://api.wulong.dev/arena-ai-leaderboards/v1/leaderboards
    Auto-updated daily snapshots. No auth required.
    """
    try:
        resp = await client.get(
            "https://api.wulong.dev/arena-ai-leaderboards/v1/leaderboards",
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        entries = data if isinstance(data, list) else (data.get("data") or data.get("models") or [])
    except Exception as exc:
        logger.warning("arena leaderboard fetch failed: %s", exc)
        return {}

    bundles: dict[str, IntelBundle] = {}
    for entry in entries:
        name = str(entry.get("model") or entry.get("model_name") or entry.get("name") or "")
        slug = _normalize_slug(name)
        if not slug:
            continue
        elo = _safe_float(entry.get("elo") or entry.get("rating") or entry.get("arena_score"))
        bundles[slug] = IntelBundle(
            model_slug=slug, arena_elo=elo,
            sources=["arena"], fetched_at=time.time(),
        )
    logger.info("arena_leaderboard: %d model bundles fetched", len(bundles))
    return bundles
```

---

### NMK-I306: `IntelFetcher` Orchestrator

```python
class IntelFetcher:
    """
    Fetches model intelligence from all sources concurrently.
    Failures in any single source are fully isolated.
    Results disk-cached at .nimmakai/intel_cache.json for restart resilience.
    
    Source priority (highest to lowest in merge):
      1. OpenRouter     (capabilities — most reliable, always available)
      2. ArtificialAnalysis (quality + speed — most accurate, optional key)
      3. HuggingFace OpenEvals (benchmark scores — no auth)
      4. Arena community (ELO scores — no auth)
    """

    def __init__(
        self, *,
        cache_path: Path = Path(".nimmakai/intel_cache.json"),
        ttl_hours: float = 6.0,
        aa_api_key: str | None = None,
    ) -> None:
        self.cache_path = cache_path
        self.ttl_hours = ttl_hours
        self.aa_api_key = aa_api_key
        self._mem_cache: dict[str, IntelBundle] | None = None
        self._mem_cache_at: float = 0.0

    async def fetch_all(self) -> dict[str, IntelBundle]:
        """Fetch all sources concurrently. Returns merged dict keyed by slug."""
        if self._mem_cache is not None:
            age_h = (time.time() - self._mem_cache_at) / 3600
            if age_h < self.ttl_hours:
                return self._mem_cache

        disk = self._load_disk_cache()

        async with httpx.AsyncClient(follow_redirects=True) as client:
            results = await asyncio.gather(
                _fetch_openrouter(client),
                _fetch_artificial_analysis(client, self.aa_api_key),
                _fetch_hf_openeval(client),
                _fetch_arena_leaderboard(client),
                return_exceptions=True,
            )

        source_names = ["openrouter", "artificialanalysis", "hf_openeval", "arena"]
        valid: list[dict[str, IntelBundle]] = []
        for i, r in enumerate(results):
            if isinstance(r, dict):
                valid.append(r)
            else:
                logger.warning("intel source [%s] failed: %s", source_names[i], r)

        merged = _merge_bundles(valid)

        # Backfill from disk for slugs not in fresh fetch
        if disk:
            for slug, b in disk.items():
                if slug not in merged:
                    merged[slug] = b

        self._mem_cache = merged
        self._mem_cache_at = time.time()
        self._save_disk_cache(merged)
        logger.info(
            "intel fetch complete: %d models, sources=%s",
            len(merged),
            sorted({s for b in merged.values() for s in b.sources}),
        )
        return merged

    def _load_disk_cache(self) -> dict[str, IntelBundle] | None:
        if not self.cache_path.is_file():
            return None
        try:
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            result: dict[str, IntelBundle] = {}
            all_field_names = {f.name for f in fields(IntelBundle)}
            for slug, d in (raw.get("bundles") or {}).items():
                b = IntelBundle(model_slug=slug, **{
                    k: v for k, v in d.items() if k in all_field_names and k != "model_slug"
                })
                result[slug] = b
            logger.info("intel disk cache loaded: %d bundles", len(result))
            return result
        except Exception as exc:
            logger.warning("intel cache load failed: %s", exc)
            return None

    def _save_disk_cache(self, bundles: dict[str, IntelBundle]) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "saved_at": time.time(),
                "bundles": {
                    slug: {f.name: getattr(b, f.name) for f in fields(b)}
                    for slug, b in bundles.items()
                },
            }
            tmp = self.cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self.cache_path)
        except Exception as exc:
            logger.warning("intel cache save failed: %s", exc)
```

---

### NMK-I307: Helpers

```python
def _normalize_slug(model_id: str) -> str:
    """Strip provider prefix + date suffix. 'openai/gpt-4o-2024-11-20' -> 'gpt-4o'"""
    import re
    bare = str(model_id or "").strip().lower().rsplit("/", 1)[-1]
    return re.sub(r"-\d{4}-\d{2}-\d{2}$", "", bare)

def _merge_bundles(sources: list[dict[str, IntelBundle]]) -> dict[str, IntelBundle]:
    """First source with a value for a field wins (highest priority first)."""
    merged: dict[str, IntelBundle] = {}
    for source in sources:
        for slug, bundle in source.items():
            if slug not in merged:
                merged[slug] = IntelBundle(model_slug=slug)
            merged[slug].merge_from(bundle)
    return merged

def _safe_float(v) -> float | None:
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None

def _safe_int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None

def _download_bytes_sync(url: str, timeout: float) -> bytes:
    """Synchronous download for asyncio.to_thread usage."""
    import urllib.request
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.read()
```

---

## Epic 4 — Model Score Cache

**Goal**: Atomic, versionable, periodically-recomputed dict of `ModelScore` objects. No QUALITY_TIERS regex, no INTENT_AFFINITY dict.  
**File**: NEW `src/nimmakai/catalog/score_cache.py`

---

### NMK-S401: `ModelScore` and `ModelScoreCache` Types

```python
@dataclass
class ModelScore:
    model_id: str
    quality: float                          # 0-100 composite
    intent_affinity: dict[str, float]       # intent -> multiplicative factor
    modalities: frozenset[str]              # "text","tools","vision","reasoning","embeddings"
    context_k: float                        # context window in K tokens
    measured_tps: float                     # 0 = unknown
    provider_id: str
    sources: list[str]
    computed_at: float

@dataclass
class ModelScoreCache:
    scores: dict[str, ModelScore]
    version: int
    computed_at: float
    live_pool: frozenset[str]

    _current: ClassVar[ModelScoreCache | None] = None

    @classmethod
    def current(cls) -> ModelScoreCache | None:
        return cls._current

    @classmethod
    def install(cls, new: ModelScoreCache) -> None:
        """Atomic reference swap. Read path never needs a lock."""
        cls._current = new

    def get(self, model_id: str) -> ModelScore | None:
        return self.scores.get(model_id)
```

---

### NMK-S402: Quality Computation (No Regex Tables)

```python
def _compute_quality(bundle: IntelBundle | None, slug: str, yaml_cfg: dict) -> float:
    cfg = yaml_cfg.get("scoring", {})
    wc = cfg.get("quality_signal_weights", {})
    bounds = cfg.get("quality_bounds", {"min": 10.0, "max": 100.0})
    arena_base = float(cfg.get("arena_elo_base", 800))
    arena_scale = float(cfg.get("arena_elo_scale", 8.0))

    w_aa  = float(wc.get("aa_intelligence", 0.40))
    w_hf  = float(wc.get("hf_leaderboard",  0.30))
    w_elo = float(wc.get("arena_elo",        0.20))
    w_par = float(wc.get("param_estimate",   0.10))

    signals: list[tuple[float, float]] = []

    if bundle:
        if bundle.aa_intelligence_idx is not None:
            signals.append((float(bundle.aa_intelligence_idx), w_aa))
        hf = _hf_composite(bundle)
        if hf is not None:
            signals.append((hf, w_hf))
        if bundle.arena_elo is not None:
            elo_norm = min(100.0, max(0.0, (bundle.arena_elo - arena_base) / arena_scale))
            signals.append((elo_norm, w_elo))
        if bundle.param_b is not None and bundle.param_b > 0:
            param_q = min(95.0, max(10.0, 60.0 + 8.0 * math.log2(bundle.param_b / 7.0)))
            signals.append((param_q, w_par))

    if signals:
        total_w = sum(w for _, w in signals)
        quality = sum(v * w for v, w in signals) / total_w
    else:
        quality = _slug_quality_fallback(slug, cfg)

    return max(float(bounds["min"]), min(float(bounds["max"]), quality))

def _hf_composite(bundle: IntelBundle) -> float | None:
    parts = [x for x in (bundle.hf_mmlu, bundle.hf_humaneval) if x is not None]
    return sum(parts) / len(parts) if parts else None

def _slug_quality_fallback(slug: str, cfg: dict) -> float:
    kws = cfg.get("quality_floor_keywords", {})
    default = float(kws.get("_default", 60.0))
    for kw, score in kws.items():
        if kw != "_default" and kw in slug:
            return float(score)
    return default
```

---

### NMK-S403: Modality Detection (No Regex Tables)

```python
def _compute_modalities(
    bundle: IntelBundle | None, model_id: str, yaml_cfg: dict
) -> frozenset[str]:
    modalities: set[str] = set()
    slug = _normalize_slug_from_id(model_id)
    mid = model_id.lower()
    hints = (yaml_cfg.get("scoring") or {}).get("capability_hints", {})

    # Hard-exclude non-chat models
    if any(_glob_match(mid, p) for p in hints.get("exclude_chat_patterns", [])):
        if "embed" in mid or "rerank" in mid:
            return frozenset({"embeddings"})
        return frozenset()

    modalities.add("text")

    if bundle:
        if bundle.supports_tools is True:   modalities.add("tools")
        if bundle.supports_vision is True:   modalities.add("vision")
        if bundle.supports_reasoning is True: modalities.add("reasoning")
        if bundle.supports_embeddings is True:
            modalities.add("embeddings")
            modalities.discard("text")

    # Fill unknowns from YAML hints
    if "tools" not in modalities and (not bundle or bundle.supports_tools is None):
        if any(_glob_match(slug, p) for p in hints.get("tools_true_patterns", [])):
            modalities.add("tools")
    if "vision" not in modalities and (not bundle or bundle.supports_vision is None):
        if any(_glob_match(slug, p) for p in hints.get("vision_true_patterns", [])):
            modalities.add("vision")
    if "reasoning" not in modalities and (not bundle or bundle.supports_reasoning is None):
        if any(_glob_match(slug, p) for p in hints.get("reasoning_true_patterns", [])):
            modalities.add("reasoning")

    return frozenset(modalities)
```

---

### NMK-S404: Intent Affinity Computation (No Static Dict)

```python
def _compute_intent_affinity(
    bundle: IntelBundle | None, modalities: frozenset[str],
    quality: float, learning, model_id: str, yaml_cfg: dict,
) -> dict[str, float]:
    intents = ["coding_agentic","reasoning","long_horizon","chat_fast","vision","embeddings"]
    deltas = (yaml_cfg.get("scoring") or {}).get("capability_affinity_deltas", {})
    affinity: dict[str, float] = {}

    for intent in intents:
        base = 1.0

        # Hard-exclude mismatched modalities
        if intent == "vision" and "vision" not in modalities:
            affinity[intent] = 0.05; continue
        if intent == "embeddings" and "embeddings" not in modalities:
            affinity[intent] = 0.05; continue
        if intent not in ("vision","embeddings") and "text" not in modalities:
            affinity[intent] = 0.05; continue

        # Apply capability deltas from YAML (all in one place, data-driven)
        if "tools" in modalities:
            base += float((deltas.get("tools_confirmed_true") or {}).get(intent, 0.0))
        elif bundle and bundle.supports_tools is False:
            base += float((deltas.get("tools_confirmed_false") or {}).get(intent, 0.0))

        if "vision" in modalities:
            base += float((deltas.get("vision_confirmed_true") or {}).get(intent, 0.0))
        elif bundle and bundle.supports_vision is False:
            base += float((deltas.get("vision_confirmed_false") or {}).get(intent, 0.0))

        if "reasoning" in modalities:
            base += float((deltas.get("reasoning_confirmed_true") or {}).get(intent, 0.0))

        ctx_k = (bundle.context_length or 0) / 1000 if bundle and bundle.context_length else 0
        if ctx_k >= 100:
            base += float((deltas.get("long_context") or {}).get(intent, 0.0))
        elif 0 < ctx_k < 16:
            base += float((deltas.get("short_context") or {}).get(intent, 0.0))

        param_b = bundle.param_b if bundle else None
        if param_b is not None:
            if param_b < 10:
                base += float((deltas.get("small_param") or {}).get(intent, 0.0))
            elif param_b > 100:
                base += float((deltas.get("frontier_param") or {}).get(intent, 0.0))

        # Quality tier boost for hard intents
        if intent in ("coding_agentic", "reasoning") and quality >= 90:
            base *= 1.10
        elif intent == "chat_fast" and quality < 70:
            base *= 1.10

        # Thompson posterior from real routing outcomes (intent-specific)
        try:
            alpha, beta_p = learning.thompson_params(intent, model_id)
            posterior_mean = alpha / (alpha + beta_p)
            posterior_factor = 0.7 + 0.6 * posterior_mean
        except Exception:
            posterior_factor = 1.0

        affinity[intent] = round(max(0.05, base * posterior_factor), 4)

    return affinity
```

---

### NMK-S405: `recompute()` — Main Entry Point

```python
def recompute(
    live_ids: set[str],
    intel_bundles: dict[str, IntelBundle],
    health,            # ModelHealthStore
    learning,          # LearningStore
    yaml_cfg: dict,
    provider_ids: set[str] | None = None,
) -> ModelScoreCache:
    """Pure function: given inputs, produce a new ModelScoreCache. No side effects."""
    provider_ids = provider_ids or set()
    scoring_cfg = yaml_cfg.get("scoring", {})
    speed_priors = scoring_cfg.get("provider_speed_prior", {})
    scores: dict[str, ModelScore] = {}

    for model_id in live_ids:
        slug = _normalize_slug_from_id(model_id)
        bundle = intel_bundles.get(slug) or _fuzzy_match_bundle(slug, intel_bundles)

        quality = _compute_quality(bundle, slug, yaml_cfg)
        modalities = _compute_modalities(bundle, model_id, yaml_cfg)
        affinity = _compute_intent_affinity(bundle, modalities, quality, learning, model_id, yaml_cfg)

        # Provider ID: split from namespaced model_id
        pid = model_id.split("/")[0] if "/" in model_id else "nim"

        # TPS: prefer measured EWMA, then AA data, then provider prior
        tps = 0.0
        h = health._by_model.get(model_id)
        if h and getattr(h, "ewma_tok_per_s", 0) > 0:
            tps = h.ewma_tok_per_s
        elif bundle and bundle.aa_tps:
            tps = bundle.aa_tps
        else:
            prior = float(speed_priors.get(pid) or speed_priors.get("_default") or 1.0)
            tps = prior * 40.0  # normalize: prior 1.0 = 40 TPS baseline

        scores[model_id] = ModelScore(
            model_id=model_id,
            quality=quality,
            intent_affinity=affinity,
            modalities=modalities,
            context_k=(bundle.context_length or 0) / 1000 if bundle else 0,
            measured_tps=tps,
            provider_id=pid,
            sources=bundle.sources if bundle else ["param_estimate"],
            computed_at=time.time(),
        )

    prev = ModelScoreCache.current()
    cache = ModelScoreCache(
        scores=scores,
        version=(prev.version + 1) if prev else 1,
        computed_at=time.time(),
        live_pool=frozenset(live_ids),
    )
    logger.info(
        "score_cache recomputed v%d: %d models, sources=%s",
        cache.version, len(scores),
        sorted({s for ms in scores.values() for s in ms.sources}),
    )
    return cache

def _normalize_slug_from_id(model_id: str) -> str:
    import re
    bare = str(model_id or "").strip().lower().rsplit("/", 1)[-1]
    return re.sub(r"-\d{4}-\d{2}-\d{2}$", "", bare)

def _fuzzy_match_bundle(slug: str, bundles: dict[str, IntelBundle]) -> IntelBundle | None:
    best: tuple[int, IntelBundle] | None = None
    for bslug, b in bundles.items():
        n = min(len(slug), len(bslug))
        common = sum(1 for i in range(n) if slug[i] == bslug[i])
        # Must share at least 4 chars AND be the best match
        if common >= 4 and (best is None or common > best[0]):
            best = (common, b)
    return best[1] if best else None

def _glob_match(s: str, pattern: str) -> bool:
    import fnmatch
    return fnmatch.fnmatch(s, pattern)
```

---

## Epic 5 — Ladder Hardcode Removal

**Goal**: Delete QUALITY_TIERS, INTENT_AFFINITY, _coding_elite_boost, INTENT_KEYWORDS. Delegate to ModelScoreCache.  
**File**: `src/nimmakai/catalog/ladder.py`

---

### NMK-L501: Delete `QUALITY_TIERS` List

Delete lines 44–161 (the entire `QUALITY_TIERS` list, `_QUALITY_COMPILED`, `_PARAM_QUALITY_SLOPE`, `_quality_from_params()`).

Replace `_base_quality()`:
```python
def _base_quality(self, mid_lower: str, model_id: str = "") -> float:
    cache = ModelScoreCache.current()
    if cache:
        ms = cache.scores.get(model_id) or cache.scores.get(mid_lower)
        if ms:
            return ms.quality
    # Cold start: param estimate
    m = PARAM_RE.search(mid_lower)
    if m:
        try:
            return min(95.0, max(10.0, 60.0 + 8.0 * math.log2(int(m.group(1)) / 7.0)))
        except (ValueError, ZeroDivisionError):
            pass
    return 65.0  # optimistic default for unknown models
```

Keep `PARAM_RE = re.compile(r"(\d+)b\b", re.I)` — cold-start fallback only.

---

### NMK-L502: Delete `INTENT_AFFINITY` Dict

Delete lines 182–270 (entire `INTENT_AFFINITY` dict).

Replace `_intent_affinity()`:
```python
def _intent_affinity(self, mid_lower: str, intent: str, model_id: str = "") -> float:
    cache = ModelScoreCache.current()
    if cache:
        ms = cache.scores.get(model_id) or cache.scores.get(mid_lower)
        if ms:
            return ms.intent_affinity.get(intent, self._default_affinity)
    return self._default_affinity
```

---

### NMK-L503: Delete `INTENT_KEYWORDS` Dict

Delete lines 279–289. Replace `_doc_keyword_bonus()` with no-op returning 0.0:
```python
def _doc_keyword_bonus(self, *args, **kwargs) -> float:
    # Superseded by Thompson-posterior-based intent affinity in score_cache.py
    return 0.0
```

---

### NMK-L504: Remove Coding-Specific Provider Prior Branch

Delete lines 606–630 (the `if intent == "coding_agentic": provider_prior = ...` block and the coding-specific speed blend).

Replace with intent-agnostic scaling:
```python
# Provider prior: uniform scaling, intent tradeoffs handled by intent_optimizer_weights
provider_prior = 1.0 + (provider_prior - 1.0) * 0.60
```

---

### NMK-L505: Delete `_coding_elite_boost()`, Remove Call Site

Delete:
1. Lines 618–638: the call site block
2. Lines 806–838: the `_coding_elite_boost()` method

The elite boost is now captured in `ModelScore.intent_affinity` via `_compute_intent_affinity()` in `score_cache.py`.

Remove `coding_boost` from the composite formula:
```python
composite = quality * affinity * capability * health_s * variant_mult * provider_prior
# coding_boost removed — absorbed into affinity from score_cache
```

---

### NMK-L506: Update `score_model()` to Pass `model_id` to Helpers

```python
def score_model(self, model_id: str, intent: str, *, variant: str = "default") -> ScoredModel:
    bare = scoring_model_id(model_id, self.provider_ids)
    mid = bare.lower()
    quality   = self._base_quality(mid, model_id)       # model_id for exact cache lookup
    affinity  = self._intent_affinity(mid, intent, model_id)
    capability = self._capability_score(model_id, mid, intent)
    health_s  = self.health.health_score(model_id)
    ...
```

---

### NMK-L507: Update `_capability_score()` to Use Modality Flags

```python
def _capability_score(self, model_id: str, mid_lower: str, intent: str) -> float:
    cache = ModelScoreCache.current()
    ms = cache.scores.get(model_id) if cache else None

    if ms:
        if intent == "vision":
            if "vision" in ms.modalities: return 1.10
            if ms.sources:  return 0.0   # confirmed absence
        elif intent == "embeddings":
            if "embeddings" in ms.modalities: return 1.10
            if ms.sources: return 0.0
        elif intent == "coding_agentic":
            if "tools" in ms.modalities: return 1.15
        elif intent == "reasoning":
            if "reasoning" in ms.modalities: return 1.20
        return 1.0

    # Fallback: probe-based caps dict (unchanged)
    caps = self.capabilities.get(model_id) or {}
    if intent == "coding_agentic":
        if caps.get("supports_tools") is True: return 1.15
        if caps.get("supports_tools") is False: return 0.10
    if intent == "vision":
        if caps.get("supports_vision") is True: return 1.10
        if caps.get("supports_vision") is False: return 0.0
    if intent == "reasoning":
        if caps.get("supports_reasoning") is True: return 1.20
    return 1.0
```

---

## Epic 6 — Routing Hardcode Removal

**Goal**: Remove all coding_agentic-specific branches from selector, fallback, auto_router, optimizer.

---

### NMK-RT601: `optimizer.py` Intent-Weighted Scoring from YAML

**File**: `src/nimmakai/routing/optimizer.py`

```python
# Module-level table, loaded at startup from yaml
_INTENT_WEIGHTS: dict[str, tuple[float, float, float, float]] = {
    "coding_agentic":  (0.50, 0.32, 0.15, 0.03),
    "reasoning":       (0.55, 0.25, 0.17, 0.03),
    "long_horizon":    (0.50, 0.28, 0.19, 0.03),
    "chat_fast":       (0.30, 0.47, 0.20, 0.03),
    "vision":          (0.45, 0.35, 0.17, 0.03),
    "embeddings":      (0.25, 0.45, 0.27, 0.03),
    "_default":        (0.45, 0.35, 0.17, 0.03),
}

def load_intent_weights(yaml_scoring: dict) -> None:
    """Called at startup after yaml is loaded."""
    global _INTENT_WEIGHTS
    for intent, w in (yaml_scoring.get("intent_optimizer_weights") or {}).items():
        try:
            _INTENT_WEIGHTS[intent] = (
                float(w["intel"]), float(w["speed"]),
                float(w["avail"]), float(w["prov"]),
            )
        except (KeyError, TypeError, ValueError):
            pass

def score_model_live(
    model_id: str, *, ladder_scores: dict, health, provider_ids: set,
    max_score: float, intent: str = "_default",
) -> float:
    alpha, beta, gamma, delta = _INTENT_WEIGHTS.get(intent, _INTENT_WEIGHTS["_default"])

    raw = ladder_scores.get(model_id, 0)
    intel = min(1.0, raw / max(max_score, 1.0)) if max_score else 0.5

    cache = ModelScoreCache.current()
    ms = cache.scores.get(model_id) if cache else None
    tps = ms.measured_tps if ms else 40.0
    speed = min(1.0, tps / 120.0)

    h = health._by_model.get(model_id)
    avail = 1.0 if h is None else max(0.01, 1.0 - h.error_rate)
    if h and h.in_cooldown():
        avail = 0.01

    prov = min(1.0, (ms.measured_tps / 40.0) * 0.5 + 0.5) if ms else 0.75

    return (intel ** alpha) * (speed ** beta) * (avail ** gamma) * (prov ** delta)

def optimize_chain(chain, *, ladder_scores, health, provider_ids, max_n=None,
                   quality_floor=0.0, intent: str = "_default") -> list[str]:
    # Pass intent through to score_model_live
    ...
```

---

### NMK-RT602: Remove 4x `coding_candidates()` Expansions from `selector.py`

**File**: `src/nimmakai/routing/selector.py`

**Block 1** (~line 267) — in alias resolution:
```python
# DELETE the entire if-block:
# if intent_key == "coding_agentic":
#     seen = set(chain)
#     for m in self.registry.coding_candidates():
#         if m not in seen: chain = chain + [m]; seen.add(m)
#     chain = self.registry.health_reorder(chain, ...)
#     head = chain[0]; rest = [m for m in chain if m != head]
```

**Block 2** (~line 342) — in passthrough branch: same deletion.

**Block 3** (~line 396) — `_resolve_auto()`:
```python
# DELETE:
# if intent_key == "coding_agentic":
#     max_n = max(max_n, int(...coding_max_fallbacks...))

# REPLACE WITH:
max_n = self._max_n_for_intent(intent_key)
```

**Block 4** (~line 491) — `_finalize_chain()`: delete coding_candidates append block.

Add shared helper:
```python
def _max_n_for_intent(self, intent: str) -> int:
    limits = getattr(self.settings, "intent_max_fallbacks", {})
    default = int(getattr(self.settings, "max_model_fallbacks", 10) or 10)
    return int(limits.get(intent, default))
```

---

### NMK-RT603: Consistent Auto Budget in `_resolve_auto()`

After NMK-RT602, auto routes get:
```python
max_n = self._max_n_for_intent(intent_key)
if is_auto:
    max_n = max(max_n, 12)  # auto gets wider safety net
```

---

### NMK-RT604: Remove 5x `coding_max_fallbacks` from `fallback.py`

**File**: `src/nimmakai/routing/fallback.py`

Add helper to `FallbackExecutor`:
```python
def _max_n_for_intent(self, intent: str) -> int:
    limits = getattr(self.settings, "intent_max_fallbacks", {})
    default = int(getattr(self.settings, "max_model_fallbacks", 10) or 10)
    return int(limits.get(intent, default))
```

Replace all 5 occurrences of:
```python
int(getattr(self.settings, "coding_max_fallbacks", 12) or 12)
```
with:
```python
self._max_n_for_intent(decision.intent.value)
```

---

### NMK-RT605: Remove `coding_candidates` from `auto_router.py`

**File**: `src/nimmakai/routing/auto_router.py`

Delete the entire block (~lines 384–388):
```python
# DELETE:
if (expand_coding_pool
    and primary in {"coding_agentic", "reasoning", "long_horizon"}
    and hasattr(registry, "coding_candidates")):
    try:
        parts.append(list(registry.coding_candidates()[:24]))
    except Exception:
        pass
```

Also remove the `expand_coding_pool: bool = True` parameter from `build_intent_aware_pool()`.

---

## Epic 7 — Health & Cooldown

**Goal**: Add 504 cooldown; make all health constants configurable.

---

### NMK-H701: Add 504 Cooldown to `record_outcome()`

**File**: `src/nimmakai/catalog/health.py`

In `record_outcome()`, add 504 case BEFORE the generic `else` error branch:
```python
elif status_code == 504:
    # Gateway timeout: upstream alive but overloaded
    # Adaptive cooldown: grows with consecutive failures (caps at 3x)
    h.error_count += 1
    h.consecutive_fails += 1
    h.consecutive_successes = 0
    h.last_fail_at = now
    cool = self.gateway_timeout_cooldown_seconds * min(h.consecutive_fails, 3)
    h.cooldown_until = max(h.cooldown_until, now + cool)
```

**Acceptance**: After one 504, model has `in_cooldown() == True` for ~30s.  
After three consecutive 504s, cooldown is 90s.

---

### NMK-H702: Wire Health Config Fields

**File**: `src/nimmakai/catalog/health.py`

Add all config fields to `ModelHealthStore.__init__()` or as dataclass fields:
```python
@dataclass
class ModelHealthStore:
    error_rate_threshold: float = 0.45
    min_samples: int = 2
    model_cooldown_seconds: float = 45.0
    hard_fail_cooldown_seconds: float = 5.0
    max_cooldown_seconds: float = 180.0
    rate_limit_cooldown_seconds: float = 15.0
    gateway_timeout_cooldown_seconds: float = 30.0
    health_window_size: int = 8
    recent_success_window_seconds: float = 30.0
    _by_model: dict = field(default_factory=dict)
    _by_pair: dict = field(default_factory=dict)
```

Wire in `main.py`:
```python
health_store = ModelHealthStore(
    error_rate_threshold=settings.error_rate_threshold,
    model_cooldown_seconds=settings.model_cooldown_seconds,
    hard_fail_cooldown_seconds=settings.hard_fail_cooldown_seconds,
    rate_limit_cooldown_seconds=settings.rate_limit_cooldown_seconds,
    gateway_timeout_cooldown_seconds=settings.gateway_timeout_cooldown_seconds,
)
```

---

## Epic 8 — Registry & Lifecycle

**Goal**: Wire IntelFetcher + ModelScoreCache into registry lifecycle.

---

### NMK-G801: `PROVIDER_SPEED_PRIOR` → Cold-Start Only

**File**: `src/nimmakai/catalog/presets.py`

Rename to `PROVIDER_SPEED_PRIOR_COLDSTART` with doc comment:
```python
# Cold-start speed priors: used ONLY until real EWMA TPS is measured (3+ outcomes).
# After that, ModelScore.measured_tps from health tracker takes over via score_cache.py.
PROVIDER_SPEED_PRIOR_COLDSTART: dict[str, float] = { ... }
```

Update `speed_prior_for_provider()` to use new name. Update all callers.

---

### NMK-G802: Delete `ZEN_FREE_CODING_MODELS`

**File**: `src/nimmakai/catalog/presets.py`

Delete lines 299–314. Models are now discovered via `/v1/models` + scored by `ModelScoreCache`.

---

### NMK-G803: Add `intent_candidates()`, Deprecate `coding_candidates()`

**File**: `src/nimmakai/catalog/registry.py`

```python
def intent_candidates(self, intent: str) -> list[str]:
    """Every live model capable of serving the given intent. All intents — no coding-only cache."""
    active = self.active_live_ids()
    if not active:
        return []
    ladder = getattr(self, "ladder", None)
    if ladder is None:
        return list(active)
    ladder_ids = set(ladder.ladder_for(intent, max_n=None))
    return [m for m in active if m in ladder_ids]

def coding_candidates(self) -> list[str]:
    """Deprecated shim. Use intent_candidates('coding_agentic')."""
    return self.intent_candidates("coding_agentic")
```

Remove `_coding_candidates_cache` and `_coding_candidates_key` attributes — no longer needed.

---

### NMK-G804: Add Intel Refresh Loop to Registry

**File**: `src/nimmakai/catalog/registry.py`

```python
def bind_intel_fetcher(self, fetcher, settings) -> None:
    self._intel_fetcher = fetcher
    self._intel_settings = settings
    self._yaml_scoring_config = self._load_yaml_scoring_config()
    self._sync_score_cache_once()   # blocking cold-start from disk

def _sync_score_cache_once(self) -> None:
    try:
        bundles = self._intel_fetcher._load_disk_cache() or {}
        cache = recompute(
            live_ids=self.active_live_ids(),
            intel_bundles=bundles,
            health=self.health, learning=self.learning,
            yaml_cfg=self._yaml_scoring_config,
            provider_ids=getattr(self.ladder, "provider_ids", set()),
        )
        ModelScoreCache.install(cache)
        logger.info("score cache cold-start: %d models from disk", len(cache.scores))
    except Exception:
        logger.exception("score cache cold-start failed")

async def start_intel_refresh_loop(self) -> None:
    interval = float(getattr(self._intel_settings, "score_recompute_interval_seconds", 300.0))
    while True:
        try:
            bundles = await self._intel_fetcher.fetch_all()
            cache = recompute(
                live_ids=self.active_live_ids(),
                intel_bundles=bundles,
                health=self.health, learning=self.learning,
                yaml_cfg=self._yaml_scoring_config,
                provider_ids=getattr(self.ladder, "provider_ids", set()),
            )
            ModelScoreCache.install(cache)
            self.ladder.rebuild(self.active_live_ids(), freeze=True)
            self._sync_chains_from_ladder()
            logger.info("intel refresh v%d: %d models", cache.version, len(cache.scores))
        except Exception:
            logger.exception("intel refresh loop failed")
        await asyncio.sleep(interval)

async def trigger_intel_refresh(self) -> dict:
    """Admin endpoint: manual refresh."""
    import asyncio
    asyncio.get_running_loop().create_task(self._do_single_refresh())
    curr = ModelScoreCache.current()
    return {"status": "refresh_queued", "current_version": curr.version if curr else 0}

async def _do_single_refresh(self) -> None:
    try:
        bundles = await self._intel_fetcher.fetch_all()
        cache = recompute(
            live_ids=self.active_live_ids(), intel_bundles=bundles,
            health=self.health, learning=self.learning,
            yaml_cfg=self._yaml_scoring_config,
            provider_ids=getattr(self.ladder, "provider_ids", set()),
        )
        ModelScoreCache.install(cache)
        self.ladder.rebuild(self.active_live_ids(), freeze=True)
    except Exception:
        logger.exception("manual intel refresh failed")
```

---

### NMK-G805: Wire in `main.py`

**File**: `src/nimmakai/main.py`

In `lifespan()` startup:
```python
from nimmakai.catalog.intel_fetcher import IntelFetcher
from nimmakai.routing.optimizer import load_intent_weights

intel_fetcher = IntelFetcher(
    cache_path=Path(getattr(settings, "intel_cache_path", ".nimmakai/intel_cache.json")),
    ttl_hours=float(getattr(settings, "intel_fetch_ttl_hours", 6.0)),
    aa_api_key=(
        getattr(settings, "artificial_analysis_api_key", "")
        or os.environ.get("ARTIFICIAL_ANALYSIS_API_KEY", "")
    ),
)
registry.bind_intel_fetcher(intel_fetcher, settings)

# Load intent optimizer weights from YAML
if hasattr(registry, "_yaml_scoring_config"):
    load_intent_weights(registry._yaml_scoring_config.get("scoring", {}))

intel_task = asyncio.create_task(registry.start_intel_refresh_loop())
```

In lifespan shutdown:
```python
intel_task.cancel()
with suppress(asyncio.CancelledError):
    await intel_task
```

---

## Epic 9 — Production Hardening

---

### NMK-P901: `/admin/intel` Status Endpoint

**File**: `src/nimmakai/routes/admin.py`

```python
@router.get("/admin/intel")
async def get_intel_status(request: Request) -> JSONResponse:
    cache = ModelScoreCache.current()
    if not cache:
        return JSONResponse({"error": "score_cache_not_initialized"}, status_code=503)

    intents = ["coding_agentic","reasoning","chat_fast","long_horizon","vision","embeddings"]
    top_by_intent = {}
    for intent in intents:
        ranked = sorted(
            cache.scores.values(),
            key=lambda ms: ms.intent_affinity.get(intent, 0) * ms.quality,
            reverse=True,
        )[:5]
        top_by_intent[intent] = [
            {"model": ms.model_id, "quality": round(ms.quality, 1),
             "affinity": round(ms.intent_affinity.get(intent, 0), 3),
             "modalities": sorted(ms.modalities), "tps": round(ms.measured_tps, 1),
             "sources": ms.sources}
            for ms in ranked
        ]

    source_counts: dict[str, int] = {}
    for ms in cache.scores.values():
        for s in ms.sources:
            source_counts[s] = source_counts.get(s, 0) + 1

    return JSONResponse({
        "version": cache.version,
        "computed_at": cache.computed_at,
        "age_seconds": round(time.time() - cache.computed_at, 1),
        "model_count": len(cache.scores),
        "live_pool_count": len(cache.live_pool),
        "source_counts": source_counts,
        "top_by_intent": top_by_intent,
    })

@router.post("/admin/intel/refresh")
async def trigger_intel_refresh(request: Request) -> JSONResponse:
    result = await request.app.state.registry.trigger_intel_refresh()
    return JSONResponse(result)

@router.get("/admin/score/{model_id:path}")
async def get_model_score(model_id: str, request: Request) -> JSONResponse:
    cache = ModelScoreCache.current()
    if not cache:
        return JSONResponse({"error": "score_cache_not_initialized"}, status_code=503)
    ms = cache.scores.get(model_id)
    if not ms:
        return JSONResponse({"error": "model_not_in_cache", "model_id": model_id}, status_code=404)
    return JSONResponse({
        "model_id": ms.model_id, "quality": ms.quality,
        "intent_affinity": ms.intent_affinity,
        "modalities": sorted(ms.modalities),
        "context_k": ms.context_k, "measured_tps": ms.measured_tps,
        "provider_id": ms.provider_id, "sources": ms.sources,
        "computed_at": ms.computed_at,
    })
```

---

### NMK-P902: `.env.example` Additions

```bash
# Dynamic Intelligence Scoring
ARTIFICIAL_ANALYSIS_API_KEY=        # Optional — enables AA benchmark data source
INTEL_FETCH_TTL_HOURS=6
INTEL_CACHE_PATH=.nimmakai/intel_cache.json
SCORE_RECOMPUTE_INTERVAL_SECONDS=300

# Fixed timeouts (504 fix)
UPSTREAM_TIMEOUT=120
REQUEST_DEADLINE_SECONDS=120
STREAM_TTFT_TIMEOUT_SECONDS=15
STREAM_IDLE_TIMEOUT_SECONDS=60
PER_ATTEMPT_BUDGET_SECONDS=30
GATEWAY_TIMEOUT_COOLDOWN_SECONDS=30

# Universal routing budget
MAX_MODEL_FALLBACKS=10
```

---

### NMK-P903: `pyarrow` Optional Dependency

**File**: `pyproject.toml`

```toml
[project.optional-dependencies]
intel = ["pyarrow>=15.0"]
```

`IntelFetcher._fetch_hf_openeval` already handles `ImportError` gracefully — system works without it.

---

### NMK-P904: Test Suite

**Files**: `tests/test_score_cache.py`, `tests/test_intel_fetcher.py`, `tests/test_fallback_504.py`

**`tests/test_score_cache.py`**:
```python
def test_recompute_empty_bundles():
    cache = recompute(
        live_ids={"nim/llama-3.1-70b"}, intel_bundles={},
        health=ModelHealthStore(), learning=LearningStore(), yaml_cfg={},
    )
    ms = cache.scores["nim/llama-3.1-70b"]
    assert 55.0 <= ms.quality <= 85.0   # param estimate for 70b

def test_recompute_aa_priority():
    bundle = IntelBundle(model_slug="llama-3.1-70b", aa_intelligence_idx=82.0)
    cache = recompute(
        live_ids={"nim/llama-3.1-70b"}, intel_bundles={"llama-3.1-70b": bundle},
        health=ModelHealthStore(), learning=LearningStore(), yaml_cfg={},
    )
    ms = cache.scores["nim/llama-3.1-70b"]
    assert abs(ms.quality - 82.0) < 5.0

def test_tools_affinity_boost():
    b_tools = IntelBundle(model_slug="model-a", supports_tools=True)
    b_none  = IntelBundle(model_slug="model-b", supports_tools=False)
    cache = recompute(
        live_ids={"p/model-a","p/model-b"},
        intel_bundles={"model-a": b_tools, "model-b": b_none},
        health=ModelHealthStore(), learning=LearningStore(), yaml_cfg={},
    )
    a = cache.scores["p/model-a"].intent_affinity["coding_agentic"]
    b = cache.scores["p/model-b"].intent_affinity["coding_agentic"]
    assert a > b, "tools-capable model must score higher for coding_agentic"

def test_atomic_install():
    c1 = recompute(live_ids=set(), intel_bundles={}, health=ModelHealthStore(),
                   learning=LearningStore(), yaml_cfg={})
    ModelScoreCache.install(c1)
    assert ModelScoreCache.current() is c1
    c2 = recompute(live_ids=set(), intel_bundles={}, health=ModelHealthStore(),
                   learning=LearningStore(), yaml_cfg={})
    ModelScoreCache.install(c2)
    assert ModelScoreCache.current() is c2
    assert c2.version == c1.version + 1
```

**`tests/test_fallback_504.py`**:
```python
import asyncio, time

async def test_504_no_sleep():
    """504 response must advance to next model in <200ms with no sleep."""
    # Setup: mock upstream that returns 504 for first model
    executor = make_test_executor(mock_responses={
        "model-a": (504, {}),
        "model-b": (200, {"choices": [...]}),
    })
    t0 = time.perf_counter()
    result = await executor.execute_json(make_decision(chain=["model-a","model-b"]))
    elapsed = time.perf_counter() - t0
    assert result.model == "model-b"
    assert elapsed < 1.0, f"Should advance fast on 504, took {elapsed:.2f}s"

async def test_504_cooldown():
    """Model that 504s gets a health cooldown."""
    health = ModelHealthStore(gateway_timeout_cooldown_seconds=30.0)
    health.record_outcome("bad-model", success=False, status_code=504)
    h = health._by_model.get("bad-model")
    assert h is not None
    assert h.in_cooldown(), "Model should be in cooldown after 504"

async def test_deadline_fixed_120s():
    """request_deadline_seconds=120 allows long streaming responses."""
    settings = make_settings(request_deadline_seconds=120.0)
    executor = FallbackExecutor(settings=settings, ...)
    deadline = executor._make_deadline()
    assert deadline > time.monotonic() + 100, "Should allow 100+ seconds"
```

---

## Ticket Dependency Graph

```
PHASE 1 — Config (no behavior change, start here):
  NMK-C101 → NMK-L501 (ladder reads from config)
  NMK-C102 → NMK-H701, NMK-H702
  NMK-C103 → NMK-RT602, NMK-RT603, NMK-RT604
  NMK-C104 → NMK-R201, NMK-R202, NMK-R203, NMK-R204
  NMK-C105 (yaml section — no code deps, do first)

PHASE 2 — 504 Fix (high-value, isolated change):
  NMK-H701 (requires NMK-C102 health fields)
  NMK-H702 (requires NMK-C102)
  NMK-R201, NMK-R202, NMK-R203, NMK-R204 (requires NMK-C104)

PHASE 3 — New Files (pure additions, no breakage):
  NMK-I301 → NMK-I302, NMK-I303, NMK-I304, NMK-I305
  NMK-I302..I305 → NMK-I306
  NMK-I306 → NMK-I307
  NMK-S401 → NMK-S402, NMK-S403, NMK-S404
  NMK-S402..S404 → NMK-S405

PHASE 4 — Ladder Rewire (requires Phase 3 complete):
  NMK-L501, NMK-L502, NMK-L503 (delete static tables, use ScoreCache)
  NMK-L504, NMK-L505           (delete coding-specific branches)
  NMK-L506, NMK-L507           (wire model_id through helpers)

PHASE 5 — Routing Cleanup (requires Phase 3+4):
  NMK-RT601 (optimizer weights from yaml)
  NMK-RT602, NMK-RT603, NMK-RT604, NMK-RT605 (remove coding branches)
  NMK-G801, NMK-G802, NMK-G803 (presets + registry cleanup)
  NMK-G804, NMK-G805 (registry lifecycle wiring)

PHASE 6 — Hardening (do last):
  NMK-P901, NMK-P902, NMK-P903, NMK-P904
```

---

## Verification Matrix

| Behavior | Verification Method | Pass Condition |
|----------|-------------------|---------------|
| 504 immediate advance | Test `test_504_no_sleep` | Next model in <200ms |
| 504 model cooldown | `health.record_outcome(status_code=504)` | `in_cooldown() == True` |
| Deadline fixed | `_make_deadline()` returns `now + 120` | `deadline > now + 100` |
| ScoreCache atomic | Concurrent reads mid-install | No exception, no torn read |
| AA quality primary | Bundle with `aa_intelligence_idx=82` | `abs(quality - 82) < 5` |
| Param fallback | Unknown model `"*70b*"` | `quality in [75, 85]` |
| Coding affinity: tools | `supports_tools=True` model | `intent_affinity["coding_agentic"] > 1.2` |
| Reasoning affinity | `supports_reasoning=True` model | `intent_affinity["reasoning"] > 1.2` |
| No coding branches | `grep -rn "coding_agentic" routing/` | Only `IntentEnum` refs |
| No `coding_max_fallbacks` | `grep -rn "coding_max_fallbacks" src/` | Zero hits |
| No `coding_candidates()` calls | `grep -rn "coding_candidates()" routing/ auto_router.py` | Zero hits |
| YAML weights respected | Set `chat_fast.speed=0.80`; restart | Chat routes faster models |
| Cache resilience | Delete `.nimmakai/intel_cache.json`; restart | Boots with param estimates |
| All tests pass | `python -m pytest tests/ -x -q --tb=short` | 0 failures |
| Admin endpoint works | `GET /admin/intel` | Returns `version`, `top_by_intent` |
| Manual refresh works | `POST /admin/intel/refresh` | Returns `refresh_queued` |

---

## Files Summary

| File | Ticket(s) | Change Type | Net Line Delta |
|------|-----------|-------------|----------------|
| `catalog/intel_fetcher.py` | NMK-I301–I307 | **NEW** | +350 |
| `catalog/score_cache.py` | NMK-S401–S405 | **NEW** | +280 |
| `catalog/ladder.py` | NMK-L501–L507, C101 | MODIFY | -250 / +80 |
| `catalog/health.py` | NMK-H701, H702, C102 | MODIFY | +35 |
| `catalog/registry.py` | NMK-G803, G804, G805 | MODIFY | +120 |
| `catalog/presets.py` | NMK-G801, G802 | MODIFY | -20 |
| `routing/optimizer.py` | NMK-RT601 | MODIFY | +40 |
| `routing/selector.py` | NMK-RT602, RT603 | MODIFY | -60 |
| `routing/fallback.py` | NMK-RT604, R201–R204 | MODIFY | -30 / +25 |
| `routing/auto_router.py` | NMK-RT605 | MODIFY | -15 |
| `config.py` | NMK-C101–C105 | MODIFY | +30 |
| `config/models.yaml` | NMK-C105 | MODIFY | +80 |
| `routes/admin.py` | NMK-P901 | MODIFY | +60 |
| `main.py` | NMK-G805, P904 | MODIFY | +25 |
| `.env.example` | NMK-P902 | MODIFY | +12 |
| `pyproject.toml` | NMK-P903 | MODIFY | +3 |
| `tests/test_score_cache.py` | NMK-P904 | **NEW** | +80 |
| `tests/test_intel_fetcher.py` | NMK-P904 | **NEW** | +60 |
| `tests/test_fallback_504.py` | NMK-P904 | **NEW** | +50 |
