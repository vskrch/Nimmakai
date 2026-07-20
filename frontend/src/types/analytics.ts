export interface AnalyticsSummary {
  total_requests: number
  success_rate: number
  avg_latency_ms: number
  p95_latency_ms: number
  avg_ttft_ms: number
  total_tokens: number
  total_prompt_tokens: number
  total_completion_tokens: number
  estimated_cost_usd: number
  unique_models: number
  active_providers: number
  top_model: string | null
  top_intent: string | null
  fallback_rate: number
  error_rate: number
  requests_per_minute: number
  time_range: { since: number; until: number }
}

export interface TraceSummary {
  id?: number
  trace_id: string
  created_at: number
  model_requested?: string | null
  model_routed?: string | null
  intent?: string | null
  intent_confidence?: number
  intent_rule_id?: string | null
  provider_id?: string | null
  status_code?: number | null
  success?: boolean
  duration_ms?: number | null
  upstream_ttft_ms?: number | null
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
  estimated_cost_usd?: number
  fallback_index?: number
  error_message?: string | null
  is_stream?: boolean
  chain?: string[]
  route_mode?: string | null
}

export interface TraceSpan {
  id?: number
  trace_id?: string
  span_type: string
  model_id?: string | null
  provider_id?: string | null
  started_at: number
  ended_at?: number | null
  duration_ms?: number | null
  status_code?: number | null
  success?: boolean
  error_message?: string | null
  metadata?: Record<string, unknown>
}

export interface TraceDetail extends TraceSummary {
  spans?: TraceSpan[]
  classify_ms?: number | null
  route_ms?: number | null
  message_count?: number
  has_tools?: boolean
  has_images?: boolean
}

export interface TraceListResponse {
  total: number
  limit: number
  offset: number
  traces: TraceSummary[]
}

export interface TimeseriesPoint {
  ts: number
  requests?: number
  success?: number
  errors?: number
  avg_ms?: number
  p50_ms?: number
  p95_ms?: number
  p99_ms?: number
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
  cost_usd?: number
  avg_ttft_ms?: number
  samples?: number
}

export interface BreakdownItem {
  key: string | number
  request_count: number
  success_count?: number
  error_count?: number
  error_rate?: number
  prompt_tokens?: number
  completion_tokens?: number
  total_tokens?: number
  cost_usd?: number
  avg_latency_ms?: number
  avg_confidence?: number
  fallback_count?: number
  status_code?: number
}

export interface LiveTraceEvent {
  type: string
  trace_id?: string
  created_at?: number
  model_routed?: string | null
  model_requested?: string | null
  intent?: string | null
  provider_id?: string | null
  status_code?: number | null
  success?: boolean
  duration_ms?: number | null
  total_tokens?: number
  fallback_index?: number
  estimated_cost_usd?: number
  error_message?: string | null
  is_stream?: boolean
}

export interface CostRatesResponse {
  defaults: { model_id: string; input_per_m: number; output_per_m: number }[]
  overrides: { model_id: string; input_per_m: number; output_per_m: number; updated_at: number }[]
}
