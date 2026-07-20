export interface HealthResponse {
  status: string
  version: string
  keys_configured: number
  keys_available: number
  active_providers: number
  live_models: number
  catalog_ok: boolean
  proxy_auth_configured: boolean
  providers: ProviderBrief[]
}

export interface ProviderBrief {
  id: string
  enabled: boolean
  key_count: number
  runtime: boolean
}

export interface StatsResponse {
  version: string
  keys: Record<string, unknown>
  providers?: Record<string, ProviderStats>
  routing?: {
    intents_total: Record<string, number>
    models_total: Record<string, number>
    fallback_advances: number
    model_tokens: Record<string, TokenStats>
    key_tokens: Record<string, TokenStats>
  }
  catalog?: {
    yaml_version: string
    live_model_count: number
    last_refresh_age_s: number | null
    last_refresh_ok: boolean
    ladders?: Record<string, LadderInfo>
  }
}

export interface ProviderStats {
  base_url: string
  enabled: boolean
  keys: Record<string, unknown>
}

export interface TokenStats {
  prompt_tokens: number
  completion_tokens: number
}

export interface LadderInfo {
  ladder_head: string[]
  ladder_len: number
  scores_head: Record<string, number>
  built_from_live: number
}

export interface Provider {
  id: string
  name: string
  base_url: string
  enabled: boolean
  rpm_limit: number
  rpd_limit: number
  max_in_flight_per_key: number
  api_style: string
  builtin: boolean
  key_count: number
  keys_masked: string[]
  runtime?: boolean
  available_keys?: number
  model_count?: number
  free_tier?: boolean
  speed_tier?: string
  signup_url?: string
}

export interface Preset {
  id: string
  name: string
  base_url: string
  free_tier?: boolean
  speed_tier?: string
  signup_url?: string
  already_configured?: boolean
  api_keys_env?: string
  custom?: boolean
}

export interface ProvidersResponse {
  providers: Provider[]
  presets: Preset[]
  pool: {
    live_models: number
    active_providers: number
    models_by_provider: Record<string, number>
  }
  pool_note?: string
}

export interface Model {
  id: string
  object: string
  created: number
  owned_by: string
  context_length?: number
}

export interface CatalogResponse {
  yaml_version: string
  live_model_count: number
  dynamic_chains: Record<string, string[]>
  ladders?: Record<string, LadderInfo>
  health?: Record<string, ModelHealthData>
}

export interface ModelHealthData {
  ewma_latency_s: number
  ewma_tok_per_s: number
  success_count: number
  error_count: number
  error_rate: number
  cooling_down: boolean
}

export interface Preference {
  intent: string
  chain: string[]
  strict: boolean
  note?: string
}

export interface RankingsResponse {
  best_coding_sticky: string[]
  best_coding_live: string[]
  score_breakdown: ScoreBreakdown[]
  best_chat: string[]
  best_reasoning: string[]
  ladders: Record<string, LadderInfo>
}

export interface ScoreBreakdown {
  model: string
  score: number
  intelligence: number
  speed: number
  health: number
  unhealthy: boolean
}

export interface ProviderHealthData {
  providers: Record<string, ProviderHealth>
}

export interface ProviderHealth {
  enabled: boolean
  runtime: boolean
  aggregate_health: number
  circuit_breaker: string
  model_count: number
  available_keys: number
  models: Record<string, ModelHealthDetail>
}

export interface ModelHealthDetail {
  ok: boolean
  ewma_latency_s: number
  ewma_tok_per_s: number
  success_count: number
  error_count: number
  error_rate: number
  cooldown: boolean
}

export interface SSEHealthEvent {
  cycle: number
  live_models: number
  active_providers: number
  fallback_advances: number
  provider_health: Record<string, { enabled: boolean; runtime: boolean; available_keys: number }>
  model_health: Record<string, { ok: boolean; tps: number; latency: number; error_rate: number }>
}
