# Dynamic Intelligence Engine: Internet-Primary, YAML Fallback

## Architecture Overview

```
┌─────────────────────────────────────────────────────┐
│                  IntelFetcher                       │
│  (catalog/intel_fetcher.py — NEW)                   │
│                                                     │
│  Primary sources (fetched async, TTL-cached):       │
│   1. OpenRouter  /api/v1/models  ─────────────────► │
│      → context_length, tools, vision, reasoning     │
│   2. ArtificialAnalysis API ──────────────────────► │
│      → intelligence_index, coding_score, speed_tps  │
│   3. HuggingFace OpenEvals dataset ───────────────► │
│      → MMLU, HumanEval, normalized 0-100            │
│   4. Arena-AI community JSON (wulong.dev) ────────► │
│      → ELO / arena rank                             │
│   5. Each provider's /v1/models (existing) ───────► │
│      → live pool, per-provider capabilities         │
│                                                     │
│  YAML fallback (models.yaml scoring section):       │
│   → static quality_floor, capability hints,         │
│     intent weights, cooldown knobs                  │
└──────────────────────┬──────────────────────────────┘
                       │ IntelBundle per model_id
                       ▼
┌─────────────────────────────────────────────────────┐
│               ModelScoreCache                       │
│  (catalog/score_cache.py — NEW)                     │
│                                                     │
│  Atomic dict[model_id → ModelScore]                 │
│  + version int + computed_at float                  │
│  Recomputed: on pool change OR every 300s           │
│  Served: O(1) dict lookup, zero lock on read        │
└──────────────────────┬──────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────┐
│   LadderService (catalog/ladder.py — MODIFIED)      │
│   Reads score_cache; runs UCB1 + Thompson on top    │
│   No QUALITY_TIERS, no INTENT_AFFINITY              │
└─────────────────────────────────────────────────────┘
```

---

## Source Priority & Merge Strategy

For every model, scores are assembled from a **waterfall** — highest-quality source wins
for each field, lower sources fill gaps:

```
quality_estimate:
  1st choice: ArtificialAnalysis intelligence_index  (0-100, real benchmarks)
  2nd choice: HuggingFace OpenEvals MMLU/HumanEval   (normalized to 0-100)
  3rd choice: Arena ELO (normalized: ELO 1000→50, 1400→95)
  4th choice: param_count log-scale (7B→60, 400B→92)  ← always available
  5th choice: YAML quality_floor[model_name_pattern]   ← static default

speed_tps:
  1st choice: ArtificialAnalysis measured TPS          (real endpoint data)
  2nd choice: EWMA from Potato's own health tracker  (observed outcomes)
  3rd choice: PROVIDER_SPEED_PRIOR in models.yaml      ← cold-start default

capability flags (tools, vision, reasoning, embeddings):
  1st choice: OpenRouter /api/v1/models ?supported_parameters=tools
  2nd choice: Provider's own /v1/models response fields
  3rd choice: ArtificialAnalysis model description keywords
  4th choice: YAML capability_hints[model_pattern]    ← static default

context_length:
  Already handled by context.py — unchanged, just wired into ModelScore
```

---

## New File: `catalog/intel_fetcher.py`

Async multi-source fetcher. Each source is a separate coroutine called concurrently.
Results are merged into `IntelBundle` objects keyed by normalized model name.

```python
@dataclass
class IntelBundle:
    """All externally-fetched intelligence data for one model."""
    model_slug: str            # normalized bare name (no provider prefix)
    
    # Quality signals (best available, None = unknown)
    aa_intelligence_idx: float | None = None   # ArtificialAnalysis 0-100
    hf_mmlu: float | None = None               # HuggingFace MMLU %
    hf_humaneval: float | None = None          # HumanEval %
    arena_elo: float | None = None             # LMSYS/community ELO
    param_b: float | None = None               # parameter count in billions
    
    # Speed signals
    aa_tps: float | None = None                # ArtificialAnalysis measured TPS
    
    # Capability flags
    supports_tools: bool | None = None
    supports_vision: bool | None = None
    supports_reasoning: bool | None = None     # has chain-of-thought / thinking
    supports_embeddings: bool | None = None
    context_length: int | None = None
    
    # Provenance (for debugging / admin UI)
    sources: list[str] = field(default_factory=list)
    fetched_at: float = 0.0
```

### Source 1: OpenRouter `/api/v1/models`
```python
async def _fetch_openrouter(client: httpx.AsyncClient) -> dict[str, IntelBundle]:
    """
    No API key needed for the public models endpoint.
    Returns rich metadata: context_length, supported_parameters, top_provider.
    """
    resp = await client.get(
        "https://openrouter.ai/api/v1/models",
        headers={"HTTP-Referer": "https://potato.ai"},
        timeout=15.0,
    )
    models = resp.json().get("data", [])
    bundles: dict[str, IntelBundle] = {}
    for m in models:
        slug = _normalize_slug(m.get("id", ""))
        sp = set(m.get("supported_parameters") or [])
        bundles[slug] = IntelBundle(
            model_slug=slug,
            context_length=m.get("context_length"),
            supports_tools="tools" in sp,
            supports_vision="image" in (m.get("architecture", {}).get("input_modalities") or []),
            supports_reasoning="reasoning" in sp or "thinking" in (m.get("description","").lower()),
            supports_embeddings=m.get("architecture", {}).get("output_modalities") == ["embeddings"],
            sources=["openrouter"],
            fetched_at=time.time(),
        )
    return bundles
```

### Source 2: Artificial Analysis API
```python
async def _fetch_artificial_analysis(
    client: httpx.AsyncClient, api_key: str | None
) -> dict[str, IntelBundle]:
    """
    https://artificialanalysis.ai/api/v1/models
    Returns intelligence_index, coding_score, output_speed (tps), latency.
    Requires AA API key (env: ARTIFICIAL_ANALYSIS_API_KEY).
    Falls back gracefully if key absent or rate-limited.
    """
    if not api_key:
        return {}
    resp = await client.get(
        "https://api.artificialanalysis.ai/v1/models",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=20.0,
    )
    bundles: dict[str, IntelBundle] = {}
    for m in (resp.json().get("models") or []):
        slug = _normalize_slug(m.get("model_id") or m.get("name", ""))
        bundles[slug] = IntelBundle(
            model_slug=slug,
            aa_intelligence_idx=m.get("intelligence_index"),
            aa_tps=m.get("output_speed"),   # tokens/s
            sources=["artificialanalysis"],
            fetched_at=time.time(),
        )
    return bundles
```

### Source 3: HuggingFace OpenEvals dataset
```python
async def _fetch_hf_openeval(client: httpx.AsyncClient) -> dict[str, IntelBundle]:
    """
    Reads the OpenEvals parquet file from HuggingFace (no auth required for public datasets).
    Columns: model, average_score, mmlu, humaneval, ...
    Cached aggressively (TTL: 24h) since parquet doesn't change per request.
    """
    # Public parquet URL — no authentication needed
    url = "https://huggingface.co/datasets/OpenEvals/leaderboard-data/resolve/main/data/train-00000-of-00001.parquet"
    # Use pyarrow/pandas in thread to avoid blocking event loop
    import asyncio, io
    raw = await asyncio.to_thread(_download_parquet, url, client)
    # Parse rows → IntelBundle dict
    ...
```

### Source 4: Arena-AI Community JSON (wulong.dev)
```python
async def _fetch_arena_leaderboard(client: httpx.AsyncClient) -> dict[str, IntelBundle]:
    """
    Community-maintained daily snapshot: https://api.wulong.dev/arena-ai-leaderboards/v1/leaderboards
    Returns arena_elo scores. No auth required.
    """
    resp = await client.get(
        "https://api.wulong.dev/arena-ai-leaderboards/v1/leaderboards",
        timeout=15.0,
    )
    ...
```

### Merge function
```python
def merge_bundles(
    *sources: dict[str, IntelBundle],  # ordered: highest priority first
) -> dict[str, IntelBundle]:
    """
    Merge multiple source dicts. For each field, take the first non-None value.
    Never overwrites a higher-priority source's value with a lower one.
    """
    merged: dict[str, IntelBundle] = {}
    for source in sources:
        for slug, bundle in source.items():
            if slug not in merged:
                merged[slug] = bundle
            else:
                existing = merged[slug]
                # Fill gaps: only set fields that are still None in existing
                for f in fields(IntelBundle):
                    if f.name in ("model_slug", "sources", "fetched_at"):
                        continue
                    if getattr(existing, f.name) is None:
                        v = getattr(bundle, f.name)
                        if v is not None:
                            object.__setattr__(existing, f.name, v)
                existing.sources = list(dict.fromkeys(existing.sources + bundle.sources))
    return merged
```

### Full fetch with error isolation
```python
class IntelFetcher:
    """
    Fetches model intelligence from all sources concurrently.
    Failures in any source are isolated — others still contribute.
    Results are disk-cached for TTL hours to survive restarts.
    """
    ttl_hours: float = 6.0          # how long before re-fetching
    cache_path: Path = Path(".potato/intel_cache.json")
    aa_api_key: str | None = None   # from env ARTIFICIAL_ANALYSIS_API_KEY
    
    async def fetch_all(self) -> dict[str, IntelBundle]:
        # Load disk cache; return immediately if fresh
        cached = self._load_cache()
        if cached and self._is_fresh(cached):
            return cached
        
        async with httpx.AsyncClient() as client:
            results = await asyncio.gather(
                _fetch_openrouter(client),
                _fetch_artificial_analysis(client, self.aa_api_key),
                _fetch_hf_openeval(client),
                _fetch_arena_leaderboard(client),
                return_exceptions=True,   # isolate failures
            )
        
        # Filter out exceptions, merge survivors highest-priority first
        valid = [r for r in results if isinstance(r, dict)]
        merged = merge_bundles(*valid)
        self._save_cache(merged)
        return merged
    
    def fetch_all_sync(self) -> dict[str, IntelBundle]:
        """Blocking wrapper for non-async startup."""
        return asyncio.run(self.fetch_all())
```

---

## New File: `catalog/score_cache.py`

Replaces `QUALITY_TIERS`, `INTENT_AFFINITY`, `_coding_elite_boost`, `PROVIDER_SPEED_PRIOR`.

```python
@dataclass
class ModelScore:
    model_id: str
    quality: float           # 0-100 composite
    intent_affinity: dict[str, float]   # intent → affinity multiplier
    modalities: frozenset[str]           # "text","tools","vision","reasoning","embeddings"
    context_k: float                     # context window in K tokens
    measured_tps: float                  # 0 = unknown
    sources: list[str]                   # provenance
    computed_at: float

@dataclass
class ModelScoreCache:
    scores: dict[str, ModelScore]   # model_id (namespaced) → score
    version: int
    computed_at: float
    live_pool: frozenset[str]
    
    # The current singleton — atomically replaced on recompute
    _current: ClassVar[ModelScoreCache | None] = None
    
    @classmethod
    def current(cls) -> ModelScoreCache | None:
        return cls._current
    
    @classmethod 
    def install(cls, new: ModelScoreCache) -> None:
        cls._current = new   # atomic reference swap

def recompute(
    live_ids: set[str],
    intel_bundles: dict[str, IntelBundle],
    health: ModelHealthStore,
    learning: LearningStore,
    yaml_config: dict,          # from models.yaml scoring section
) -> ModelScoreCache:
    """
    Pure function: given inputs, produce a new ModelScoreCache.
    No side effects. Called from a background task.
    """
    scores: dict[str, ModelScore] = {}
    
    for model_id in live_ids:
        slug = _bare_slug(model_id)          # strip provider prefix
        bundle = intel_bundles.get(slug) or _match_fuzzy(slug, intel_bundles)
        
        # ── 1. Quality estimate ─────────────────────────────────
        quality = _compute_quality(bundle, slug, yaml_config)
        
        # ── 2. Modality flags ──────────────────────────────────
        modalities = _compute_modalities(bundle, model_id, health, learning)
        
        # ── 3. Intent affinity (data-driven, no hardcoded matrix) ─
        affinity = _compute_intent_affinity(bundle, modalities, quality, learning, model_id)
        
        # ── 4. Speed / TPS ─────────────────────────────────────
        tps = _compute_tps(bundle, model_id, health, yaml_config)
        
        scores[model_id] = ModelScore(
            model_id=model_id,
            quality=quality,
            intent_affinity=affinity,
            modalities=modalities,
            context_k=(bundle.context_length or 0) / 1000 if bundle else 0,
            measured_tps=tps,
            sources=bundle.sources if bundle else ["param_estimate"],
            computed_at=time.time(),
        )
    
    return ModelScoreCache(
        scores=scores,
        version=(ModelScoreCache.current().version + 1) if ModelScoreCache.current() else 1,
        computed_at=time.time(),
        live_pool=frozenset(live_ids),
    )
```

### Quality Computation (no regex tables)
```python
def _compute_quality(bundle: IntelBundle | None, slug: str, yaml: dict) -> float:
    """
    Merge multiple quality signals from highest-fidelity to lowest.
    All bounds from yaml config (scoring.quality_bounds).
    """
    cfg = yaml.get("scoring", {})
    bounds = cfg.get("quality_bounds", {"min": 10.0, "max": 100.0})
    
    signals: list[tuple[float, float]] = []   # (value, weight)
    
    if bundle:
        # AA intelligence index: already on 0-100 scale, high fidelity
        if bundle.aa_intelligence_idx is not None:
            signals.append((bundle.aa_intelligence_idx, 0.40))
        
        # HF leaderboard: MMLU+HumanEval average normalized to 0-100
        hf = _hf_composite(bundle)
        if hf is not None:
            signals.append((hf, 0.30))
        
        # Arena ELO: normalize ELO range → 0-100
        if bundle.arena_elo is not None:
            elo_norm = min(100.0, max(0.0, (bundle.arena_elo - 800) / 8.0))
            signals.append((elo_norm, 0.20))
        
        # Param count: log-scale estimate (lowest fidelity external signal)
        if bundle.param_b is not None:
            param_q = min(95.0, max(10.0, 60.0 + 8.0 * math.log2(bundle.param_b / 7.0)))
            signals.append((param_q, 0.10))
    
    if signals:
        total_w = sum(w for _, w in signals)
        quality = sum(v * w for v, w in signals) / total_w
    else:
        # No external data: estimate from slug tokens only
        quality = _slug_quality_fallback(slug, yaml)   # uses yaml quality_floor section
    
    return max(bounds["min"], min(bounds["max"], quality))
```

### Intent Affinity Computation (no static dict)
```python
def _compute_intent_affinity(
    bundle: IntelBundle | None,
    modalities: frozenset[str],
    quality: float,
    learning: LearningStore,
    model_id: str,
) -> dict[str, float]:
    """
    Data-driven affinity for each intent:
      base = capability match score (from modalities)
      × posterior (Thompson mean for this intent from outcomes)
      × quality_tier_factor (frontier models get slight boost on hard tasks)
    """
    intents = ["coding_agentic", "reasoning", "long_horizon", "chat_fast", "vision", "embeddings"]
    affinity: dict[str, float] = {}
    
    for intent in intents:
        base = 1.0
        
        # Modality match: does this model have what the intent needs?
        if intent == "vision":
            base = 1.30 if "vision" in modalities else 0.10
        elif intent == "embeddings":
            base = 1.30 if "embeddings" in modalities else 0.05
        elif intent == "coding_agentic":
            if "tools" in modalities:
                base = 1.25   # confirmed tool support
            elif "tools" not in modalities and bundle and bundle.supports_tools is False:
                base = 0.20   # confirmed no tools
        elif intent == "reasoning":
            if "reasoning" in modalities:
                base = 1.30   # confirmed chain-of-thought
        elif intent == "long_horizon":
            ctx_k = (bundle.context_length or 0) / 1000 if bundle else 0
            if ctx_k >= 100:
                base = 1.25   # long context confirmed
            elif ctx_k > 0 and ctx_k < 16:
                base = 0.70   # too short for long tasks
        elif intent == "chat_fast":
            if quality < 70:
                base = 1.15   # fast small models suit chat
        
        # Quality tier: frontier models (quality >= 90) are better at hard tasks
        if intent in ("coding_agentic", "reasoning") and quality >= 90:
            base *= 1.10
        
        # Thompson posterior from real routing outcomes (intent-specific)
        alpha, beta = learning.thompson_params(intent, model_id)
        posterior_mean = alpha / (alpha + beta)
        # Scale: 0.5 posterior = 1.0x affinity, 1.0 = 1.3x, 0.0 = 0.7x
        posterior_factor = 0.7 + 0.6 * posterior_mean
        
        affinity[intent] = round(base * posterior_factor, 4)
    
    return affinity
```

---

## Modified: `catalog/ladder.py`

**DELETE** these ~200 lines of hardcoded data:
- `QUALITY_TIERS` list (lines 44-154)
- `INTENT_AFFINITY` dict (lines 182-270)
- `INTENT_KEYWORDS` dict (lines 279-289)
- `_coding_elite_boost()` method (lines 806-838)
- `UCB_C = 5.0` → `self._ucb_c` from `config`
- `_MAX_HEAD_FAMILY_STREAK = 2` → `self._diversity_streak` from `config`
- `_DEFAULT_AFFINITY = 0.85` → from `config`

**KEEP** (unchanged algorithms):
- `_build_ladder()` greedy sort + diversity
- `_ucb_bonus()` UCB1 formula
- `_thompson_bonus()` Thompson sampling  
- `LadderSnapshot`, `ScoredModel` types
- `health_reorder()` (unchanged)

**MODIFY `score_model()`**:
```python
def score_model(self, model_id: str, intent: str, *, variant: str = "default") -> ScoredModel:
    sc = ModelScoreCache.current()
    ms = sc.scores.get(model_id) if sc else None
    
    # ── Quality + affinity from live score cache ────────────
    if ms:
        quality = ms.quality
        affinity = ms.intent_affinity.get(intent, self._default_affinity)
        # Modality hard-block: use computed flags, not regex
        if intent == "vision" and "vision" not in ms.modalities:
            return ScoredModel(model_id, -1e9, reasons=["not_vision"])
        if intent == "embeddings" and "embeddings" not in ms.modalities:
            return ScoredModel(model_id, -1e9, reasons=["not_embedding"])
        # Exclude non-chat (embed/rerank) from chat intents
        if intent not in ("vision", "embeddings") and "text" not in ms.modalities:
            return ScoredModel(model_id, -1e9, reasons=["excluded_modality"])
    else:
        # No cache entry: unknown model, use optimistic defaults
        quality = 65.0
        affinity = self._default_affinity
    
    # ── Health ──────────────────────────────────────────────
    health_s = self.health.health_score(model_id)
    
    # ── Variant multiplier ──────────────────────────────────
    variant_mult = self._variant_mult(model_id, ms, variant)
    
    # ── UCB1 + Thompson (algorithms unchanged) ──────────────
    ucb = self._ucb_bonus(model_id, intent)
    thompson = self._thompson_bonus(model_id, intent)
    
    composite = quality * affinity * health_s * variant_mult
    return ScoredModel(model_id, composite + ucb + thompson, quality=quality,
                       affinity=affinity, health=health_s, ...)
```

---

## Modified: `config.py`

All magic numbers externalized:

```python
# Timeouts (fix the 504 cascade)
upstream_timeout: float = 120.0
stream_ttft_timeout_seconds: float = 15.0
stream_idle_timeout_seconds: float = 60.0
request_deadline_seconds: float = 120.0

# Routing
max_model_fallbacks: int = 10    # universal (coding_max_fallbacks REMOVED)
per_attempt_budget_seconds: float = 30.0

# Health
gateway_timeout_cooldown_seconds: float = 30.0
rate_limit_cooldown_seconds: float = 15.0
hard_fail_cooldown_seconds: float = 5.0
model_cooldown_seconds: float = 45.0
error_rate_threshold: float = 0.45

# Scoring
ucb_exploration_c: float = 5.0
diversity_streak_max: int = 2
default_affinity: float = 0.85

# Intelligence fetching
intel_fetch_ttl_hours: float = 6.0
intel_fetch_on_startup: bool = True
score_recompute_interval_seconds: float = 300.0
artificial_analysis_api_key: str = ""   # from ARTIFICIAL_ANALYSIS_API_KEY env
```

---

## Modified: `config/models.yaml`

YAML becomes the **fallback + tuning** layer. Operators configure weights, bounds, and
per-model overrides here — but the system works without it using pure internet data.

```yaml
scoring:
  # Quality signal weights (for blending multiple sources)
  quality_signal_weights:
    aa_intelligence:  0.40   # ArtificialAnalysis index (highest fidelity)
    hf_leaderboard:  0.30   # HuggingFace OpenEvals MMLU/HumanEval
    arena_elo:       0.20   # LMSYS/community arena rank
    param_estimate:  0.10   # log-scale fallback

  quality_bounds:
    min: 10.0
    max: 100.0

  # Per-intent optimizer weights (intel + speed + avail + prov = 1.0)
  # These govern HOW the routing decides, independent of model quality
  intent_optimizer_weights:
    coding_agentic:  {intel: 0.50, speed: 0.32, avail: 0.15, prov: 0.03}
    reasoning:       {intel: 0.55, speed: 0.25, avail: 0.17, prov: 0.03}
    long_horizon:    {intel: 0.50, speed: 0.28, avail: 0.19, prov: 0.03}
    chat_fast:       {intel: 0.30, speed: 0.47, avail: 0.20, prov: 0.03}
    vision:          {intel: 0.45, speed: 0.35, avail: 0.17, prov: 0.03}
    embeddings:      {intel: 0.25, speed: 0.45, avail: 0.27, prov: 0.03}
    _default:        {intel: 0.45, speed: 0.35, avail: 0.17, prov: 0.03}

  # Per-intent fallback chain budget
  intent_max_fallbacks:
    coding_agentic: 10
    reasoning:      10
    long_horizon:   8
    chat_fast:      6
    vision:         6
    embeddings:     4

  # Cold-start provider speed priors (replaced by measured TPS once data exists)
  provider_speed_prior:
    groq:       1.35
    cerebras:   1.40
    sambanova:  1.30
    zen:        1.28
    deepseek:   1.25
    fireworks:  1.20
    together:   1.15
    nim:        1.05

  # Static quality floor by slug pattern (last resort when no external data)
  # Format: {pattern: floor_score}  — used ONLY when no internet source matched
  quality_floor:
    ".*70b.*":   78.0
    ".*400b.*":  92.0
    ".*8b.*":    64.0
    ".*instruct.*":  65.0
    _default:    60.0

  # Capability hints (fallback when neither OpenRouter nor provider reports capabilities)
  capability_hints:
    tools_true:  ["*gpt*", "*claude*", "*gemini*", "*deepseek*", "*qwen*"]
    vision_true: ["*vision*", "*vl*", "*omni*", "*gemini*"]
    reasoning_true: ["*r1*", "*o1*", "*o3*", "*deepseek-r1*", "*thinking*"]
    embed_true:  ["*embed*", "*rerank*", "*retrieval*"]
```

---

## Registry Integration

`ModelRegistry` gets two new methods:

```python
def start_intel_refresh_loop(self, settings) -> None:
    """
    Background task: fetch intel, recompute scores, rebuild ladder.
    Runs on startup and then every score_recompute_interval_seconds.
    """
    import asyncio
    loop = asyncio.get_event_loop()
    loop.create_task(self._intel_refresh_loop(settings))

async def _intel_refresh_loop(self, settings) -> None:
    fetcher = IntelFetcher(
        ttl_hours=settings.intel_fetch_ttl_hours,
        aa_api_key=settings.artificial_analysis_api_key,
    )
    while True:
        try:
            bundles = await fetcher.fetch_all()
            new_cache = recompute(
                live_ids=self.active_live_ids(),
                intel_bundles=bundles,
                health=self.health,
                learning=self.learning,
                yaml_config=self._yaml_scoring_config,
            )
            ModelScoreCache.install(new_cache)
            self.ladder.rebuild(self.active_live_ids(), freeze=True)
            logger.info("intel refresh: %d models scored (sources: %s)",
                        len(new_cache.scores),
                        set(s for ms in new_cache.scores.values() for s in ms.sources))
        except Exception:
            logger.exception("intel refresh failed; using existing cache")
        
        await asyncio.sleep(settings.score_recompute_interval_seconds)
```

---

## Disk Cache for Resilience

`IntelFetcher` persists fetched bundles to `.potato/intel_cache.json`. On startup:
1. Load from disk immediately (instant scoring with stale but real data)
2. Background-fetch fresh data from all sources
3. Install new cache when ready → ladder rebuilds → routing instantly improves

This means **503/504s don't affect cold-start quality** — the last-known intelligence
snapshot is always available.

---

## Files Changed

| File | Type | Key Change |
|------|------|------------|
| `catalog/intel_fetcher.py` | **NEW** | 4-source async fetcher, merge, disk cache |
| `catalog/score_cache.py` | **NEW** | ModelScoreCache, recompute(), atomic install |
| `catalog/ladder.py` | MODIFY | Delete QUALITY_TIERS, INTENT_AFFINITY, _coding_elite_boost; use ScoreCache |
| `catalog/health.py` | MODIFY | Externalize all magic numbers; add 504 cooldown |
| `catalog/registry.py` | MODIFY | Add intel_refresh_loop, intent_candidates, score_cache wiring |
| `catalog/presets.py` | MODIFY | Remove ZEN_FREE_CODING_MODELS; speed prior → yaml only |
| `routing/optimizer.py` | MODIFY | INTENT_WEIGHTS from yaml, intent-aware scoring |
| `routing/selector.py` | MODIFY | Remove 4× coding_candidates blocks |
| `routing/fallback.py` | MODIFY | 504 fix, remove coding_max_fallbacks, intent budget |
| `routing/auto_router.py` | MODIFY | Remove coding_candidates pool expansion |
| `config.py` | MODIFY | Fix timeouts, add intel/scoring knobs, remove coding_max_fallbacks |
| `config/models.yaml` | MODIFY | Add full scoring section as structured fallback |

---

## Quality Guarantee (Hard & Reasoning Tasks)

| Signal Layer | Ensures Top Models Win |
|---|---|
| ArtificialAnalysis `intelligence_index` | Coding+reasoning benchmark composite 0-100 → frontier models always score 85-99 |
| `supports_reasoning=True` from OpenRouter | Reasoning models get `intent_affinity["reasoning"] × 1.30` |
| `supports_tools=True` from OpenRouter | Coding-capable models get `intent_affinity["coding_agentic"] × 1.25` |
| Thompson posterior (real outcomes) | Models that succeed on hard tasks gain `posterior_factor > 1.0` over time |
| UCB1 exploration bonus | New/untested strong models get a chance, not blocked by stale data |
| YAML `quality_floor` | Last-resort: even with no internet data, sensible defaults prevent garbage models leading |

---

## Verification Plan

```bash
# 1. Unit tests
cd /Users/venkatasai/CascadeProjects/Potato
python -m pytest tests/ -x -q --tb=short

# 2. Intel fetcher smoke test (offline-safe)
python -c "
import asyncio
from potato.catalog.intel_fetcher import IntelFetcher
f = IntelFetcher()
bundles = asyncio.run(f.fetch_all())
print(f'Fetched {len(bundles)} bundles')
for name, b in list(bundles.items())[:5]:
    print(f'  {name}: aa_idx={b.aa_intelligence_idx}, tools={b.supports_tools}')
"

# 3. Score cache smoke test
python -c "
from potato.catalog.score_cache import recompute
# ... construct minimal test
"

# 4. End-to-end routing quality check
python -m potato &
curl http://localhost:8080/admin/rankings | python -m json.tool | head -60
# Verify: coding_agentic top models have quality > 85 and sources include 'artificialanalysis'

# 5. 504 fast-advance test (manual)
# Block one upstream temporarily; verify X-Potato-Fallback-Index increments fast
```
