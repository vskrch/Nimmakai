import { useState, useEffect, useCallback, useRef } from 'react'
import { api, ap, ad, errMsg } from '../lib/api'
import type {
  HealthResponse, StatsResponse, ProvidersResponse, CatalogResponse,
  RankingsResponse, ProviderHealthData, Preference, SSEHealthEvent
} from '../types'

export function useAuth() {
  const [authed, setAuthed] = useState(() => !!localStorage.getItem('nk'))
  const [showAuth, setShowAuth] = useState(() => !localStorage.getItem('nk'))

  const doAuth = useCallback(async (key: string) => {
    localStorage.setItem('nk', key)
    const r = await api('/stats')
    if (r && (r as Record<string, unknown>).__ok === false) {
      localStorage.removeItem('nk')
      setAuthed(false)
      return false
    }
    setAuthed(true)
    setShowAuth(false)
    return true
  }, [])

  const logout = useCallback(() => {
    localStorage.removeItem('nk')
    setAuthed(false)
    setShowAuth(true)
  }, [])

  return { authed, showAuth, setShowAuth, doAuth, logout }
}

export function useHealth() {
  const [data, setData] = useState<HealthResponse | null>(null)
  const load = useCallback(async () => {
    const r = await api<HealthResponse>('/health')
    if (r) setData(r)
  }, [])
  useEffect(() => { load() }, [load])
  return { data, reload: load }
}

export function useStats() {
  const [data, setData] = useState<StatsResponse | null>(null)
  const load = useCallback(async () => {
    const r = await api<StatsResponse>('/stats')
    if (r) setData(r)
  }, [])
  useEffect(() => { load() }, [load])
  return { data, reload: load }
}

export function useProviders() {
  const [data, setData] = useState<ProvidersResponse | null>(null)
  const load = useCallback(async () => {
    const r = await api<ProvidersResponse>('/admin/providers')
    if (r) setData(r)
  }, [])
  useEffect(() => { load() }, [load])
  return { data, reload: load }
}

export function useCatalog() {
  const [data, setData] = useState<CatalogResponse | null>(null)
  const load = useCallback(async () => {
    const r = await api<CatalogResponse>('/catalog')
    if (r) setData(r)
  }, [])
  useEffect(() => { load() }, [load])
  return { data, reload: load }
}

export function useRankings() {
  const [data, setData] = useState<RankingsResponse | null>(null)
  const load = useCallback(async () => {
    const r = await api<RankingsResponse>('/admin/rankings')
    if (r) setData(r)
  }, [])
  useEffect(() => { load() }, [load])
  return { data, reload: load }
}

export function useProviderHealth() {
  const [data, setData] = useState<ProviderHealthData | null>(null)
  const load = useCallback(async () => {
    const r = await api<ProviderHealthData>('/admin/health/providers')
    if (r) setData(r)
  }, [])
  useEffect(() => { load() }, [load])
  return { data, reload: load }
}

export function usePreferences() {
  const [prefs, setPrefs] = useState<Preference[]>([])
  const load = useCallback(async () => {
    const r = await api<{ preferences: Preference[] }>('/preferences')
    if (r) setPrefs(r.preferences)
  }, [])
  useEffect(() => { load() }, [load])
  return { prefs, reload: load }
}

export function useSSE() {
  const ref = useRef<EventSource | null>(null)
  const [event, setEvent] = useState<SSEHealthEvent | null>(null)

  useEffect(() => {
    const es = new EventSource('/admin/events')
    es.addEventListener('health', (e) => {
      try { setEvent(JSON.parse(e.data)) } catch { /* ignore */ }
    })
    es.onerror = () => {
      setTimeout(() => {
        if (ref.current === es) {
          ref.current = new EventSource('/admin/events')
        }
      }, 5000)
    }
    ref.current = es
    return () => { es.close(); ref.current = null }
  }, [])

  return event
}
