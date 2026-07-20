import { useState, useEffect, useCallback, useRef } from 'react'
import { api, getAuthKey, okBody } from '../lib/api'
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
  const load = useCallback(async () => {
    setLoading(true)
    const r = await api<AnalyticsSummary>(`/analytics/summary${qs({ since: rangeSince(range) })}`)
    if (okBody(r)) setData(r as AnalyticsSummary)
    setLoading(false)
  }, [range])
  useEffect(() => { load() }, [load])
  return { data, loading, reload: load }
}

export function useTimeseries(metric: string, range = '1h', interval = '5m') {
  const [points, setPoints] = useState<TimeseriesPoint[]>([])
  const load = useCallback(async () => {
    const r = await api<{ points: TimeseriesPoint[] }>(
      `/analytics/timeseries/${metric}${qs({ since: rangeSince(range), interval })}`
    )
    if (r?.points) setPoints(r.points)
  }, [metric, range, interval])
  useEffect(() => { load() }, [load])
  return { points, reload: load }
}

export function useBreakdown(dimension: string, range = '24h') {
  const [items, setItems] = useState<BreakdownItem[]>([])
  const load = useCallback(async () => {
    const r = await api<{ items: BreakdownItem[] }>(
      `/analytics/breakdown/${dimension}${qs({ since: rangeSince(range), limit: 30 })}`
    )
    if (r?.items) setItems(r.items)
  }, [dimension, range])
  useEffect(() => { load() }, [load])
  return { items, reload: load }
}

export function useTraces(filters: Record<string, string | number | undefined>) {
  const [data, setData] = useState<TraceListResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const load = useCallback(async () => {
    setLoading(true)
    const r = await api<TraceListResponse>(`/analytics/traces${qs(filters)}`)
    if (okBody(r)) setData(r as TraceListResponse)
    setLoading(false)
  }, [JSON.stringify(filters)])
  useEffect(() => { load() }, [load])
  return { data, loading, reload: load }
}

export function useTraceDetail(traceId: string | null) {
  const [detail, setDetail] = useState<TraceDetail | null>(null)
  const [loading, setLoading] = useState(false)
  useEffect(() => {
    if (!traceId) {
      setDetail(null)
      return
    }
    let cancelled = false
    ;(async () => {
      setLoading(true)
      const r = await api<TraceDetail>(`/analytics/traces/${encodeURIComponent(traceId)}`)
      if (!cancelled && okBody(r)) setDetail(r as TraceDetail)
      setLoading(false)
    })()
    return () => { cancelled = true }
  }, [traceId])
  return { detail, loading }
}

export function useCostRates() {
  const [data, setData] = useState<CostRatesResponse | null>(null)
  const load = useCallback(async () => {
    const r = await api<CostRatesResponse>('/analytics/cost/rates')
    if (okBody(r)) setData(r as CostRatesResponse)
  }, [])
  useEffect(() => { load() }, [load])
  return { data, reload: load }
}

export function useAnalyticsSSE(enabled = true) {
  const [connected, setConnected] = useState(false)
  const [events, setEvents] = useState<LiveTraceEvent[]>([])
  const [paused, setPaused] = useState(false)
  const buffer = useRef<LiveTraceEvent[]>([])
  const pausedRef = useRef(false)
  pausedRef.current = paused

  useEffect(() => {
    if (!enabled) return
    const key = getAuthKey()
    const url = key ? `/analytics/events?token=${encodeURIComponent(key)}` : '/analytics/events'
    const es = new EventSource(url)
    es.onopen = () => setConnected(true)
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
    es.onerror = () => setConnected(false)
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

  return { connected, events, paused, togglePause, setEvents }
}
