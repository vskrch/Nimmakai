import { useState, useEffect, useCallback, useRef } from 'react'
import { api, getAuthKey, okBody, errMsg } from '../lib/api'
import { qs, rangeSince } from '../lib/format'
import type {
  AnalyticsSummary,
  TraceListResponse,
  TraceDetail,
  TimeseriesPoint,
  BreakdownItem,
  LiveTraceEvent,
  CostRatesResponse,
} from '../types/analytics'

export function useAnalyticsSummary(range = '1h') {
  const [data, setData] = useState<AnalyticsSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    const r = await api<AnalyticsSummary>(`/analytics/summary${qs({ since: rangeSince(range) })}`)
    if (okBody(r)) setData(r as AnalyticsSummary)
    else {
      setError(errMsg(r) || 'Failed to load summary')
      setData(null)
    }
    setLoading(false)
  }, [range])
  useEffect(() => { load() }, [load])
  return { data, loading, error, reload: load }
}

export function useTimeseries(metric: string, range = '1h', interval = '5m') {
  const [points, setPoints] = useState<TimeseriesPoint[]>([])
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(async () => {
    setError(null)
    const r = await api<{ points: TimeseriesPoint[] }>(
      `/analytics/timeseries/${metric}${qs({ since: rangeSince(range), interval })}`
    )
    if (r?.points) setPoints(r.points)
    else {
      setError(errMsg(r) || 'Failed to load timeseries')
      setPoints([])
    }
  }, [metric, range, interval])
  useEffect(() => { load() }, [load])
  return { points, error, reload: load }
}

export function useBreakdown(dimension: string, range = '24h') {
  const [items, setItems] = useState<BreakdownItem[]>([])
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(async () => {
    setError(null)
    const r = await api<{ items: BreakdownItem[] }>(
      `/analytics/breakdown/${dimension}${qs({ since: rangeSince(range), limit: 30 })}`
    )
    if (r?.items) setItems(r.items)
    else {
      setError(errMsg(r) || 'Failed to load breakdown')
      setItems([])
    }
  }, [dimension, range])
  useEffect(() => { load() }, [load])
  return { items, error, reload: load }
}

/** Stable filter keys — never put wall-clock `rangeSince()` in deps (refetch loop). */
export function useTraces(filters: {
  range?: string
  limit?: number
  offset?: number
  search?: string
  status?: string
  intent?: string
  model?: string
  provider?: string
}) {
  const [data, setData] = useState<TraceListResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const {
    range = '1h',
    limit = 40,
    offset = 0,
    search,
    status,
    intent,
    model,
    provider,
  } = filters

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    const r = await api<TraceListResponse>(
      `/analytics/traces${qs({
        since: rangeSince(range),
        limit,
        offset,
        search: search || undefined,
        status: status || undefined,
        intent: intent || undefined,
        model: model || undefined,
        provider: provider || undefined,
      })}`
    )
    if (okBody(r)) setData(r as TraceListResponse)
    else {
      setError(errMsg(r) || 'Failed to load traces')
      setData(null)
    }
    setLoading(false)
  }, [range, limit, offset, search, status, intent, model, provider])
  useEffect(() => { load() }, [load])
  return { data, loading, error, reload: load }
}

export function useTraceDetail(traceId: string | null) {
  const [detail, setDetail] = useState<TraceDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    if (!traceId) {
      setDetail(null)
      setError(null)
      return
    }
    let cancelled = false
    ;(async () => {
      setLoading(true)
      setError(null)
      setDetail(null)
      const r = await api<TraceDetail>(`/analytics/traces/${encodeURIComponent(traceId)}`)
      if (cancelled) return
      if (okBody(r)) setDetail(r as TraceDetail)
      else setError(errMsg(r) || 'Trace not found')
      setLoading(false)
    })()
    return () => { cancelled = true }
  }, [traceId])
  return { detail, loading, error }
}

export function useCostRates() {
  const [data, setData] = useState<CostRatesResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    const r = await api<CostRatesResponse>('/analytics/cost/rates')
    if (okBody(r)) setData(r as CostRatesResponse)
    else {
      setError(errMsg(r) || 'Failed to load rates')
      setData(null)
    }
    setLoading(false)
  }, [])
  useEffect(() => { load() }, [load])
  return { data, loading, error, reload: load }
}

export function useAnalyticsSSE(enabled = true) {
  const [connected, setConnected] = useState(false)
  const [events, setEvents] = useState<LiveTraceEvent[]>([])
  const [paused, setPaused] = useState(false)
  const [authError, setAuthError] = useState(false)
  const buffer = useRef<LiveTraceEvent[]>([])
  const pausedRef = useRef(false)
  const errorCount = useRef(0)
  pausedRef.current = paused

  useEffect(() => {
    if (!enabled) return
    const key = getAuthKey()
    const url = key ? `/analytics/events?token=${encodeURIComponent(key)}` : '/analytics/events'
    const es = new EventSource(url)
    es.onopen = () => {
      setConnected(true)
      setAuthError(false)
      errorCount.current = 0
    }
    es.onmessage = (e) => {
      try {
        const payload = JSON.parse(e.data) as LiveTraceEvent
        if (payload.type !== 'trace') return
        if (pausedRef.current) {
          buffer.current = [payload, ...buffer.current].slice(0, 100)
          return
        }
        setEvents(prev => [payload, ...prev].slice(0, 100))
      } catch { /* ignore */ }
    }
    es.onerror = () => {
      setConnected(false)
      errorCount.current += 1
      // EventSource auto-retries; after repeated failures surface reconnect help
      // without claiming a specific auth failure (network blips are common).
      if (errorCount.current >= 5) {
        setAuthError(true)
        es.close()
      }
    }
    return () => { es.close(); setConnected(false) }
  }, [enabled])

  const togglePause = useCallback(() => {
    setPaused(p => {
      if (p && buffer.current.length) {
        setEvents(prev => [...buffer.current, ...prev].slice(0, 100))
        buffer.current = []
      }
      return !p
    })
  }, [])

  return { connected, events, paused, togglePause, setEvents, authError }
}
