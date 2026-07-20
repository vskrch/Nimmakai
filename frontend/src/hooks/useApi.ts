import { useState, useEffect, useCallback, useRef } from 'react'
import { api, ap, clearAuthKey } from '../lib/api'
import type { AuthSession } from '../components/AuthModal'
import type {
  HealthResponse, StatsResponse, ProvidersResponse, CatalogResponse,
  RankingsResponse, ProviderHealthData, Preference, SSEHealthEvent
} from '../types'

export function useAuth() {
  const [session, setSession] = useState<AuthSession | null>(null)
  const [authed, setAuthed] = useState(false)
  const [showAuth, setShowAuth] = useState(true)
  const [ready, setReady] = useState(false)

  const applySession = useCallback((me: AuthSession) => {
    setSession(me)
    setAuthed(true)
    setShowAuth(false)
  }, [])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      const me = await api<AuthSession>('/auth/me')
      if (cancelled) return
      if (me?.authenticated) {
        applySession(me)
      } else if (localStorage.getItem('nk')) {
        const stats = await api('/stats')
        if (cancelled) return
        if (stats && (stats as { __ok?: boolean }).__ok !== false) {
          applySession({
            authenticated: true,
            is_admin: true,
            via: 'legacy_proxy',
            user: { id: null, email: null, role: 'admin', status: 'active' },
          })
        } else {
          clearAuthKey()
          setShowAuth(true)
        }
      } else {
        setShowAuth(true)
      }
      setReady(true)
    })()
    return () => { cancelled = true }
  }, [applySession])

  const logout = useCallback(async () => {
    await ap('/auth/logout', {})
    clearAuthKey()
    setSession(null)
    setAuthed(false)
    setShowAuth(true)
  }, [])

  return {
    ready,
    authed,
    showAuth,
    setShowAuth,
    session,
    applySession,
    logout,
    isAdmin: !!session?.is_admin,
    status: session?.user?.status || null,
    email: session?.user?.email || null,
  }
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
    const key = localStorage.getItem('nk') || ''
    const url = key ? `/admin/events?token=${encodeURIComponent(key)}` : '/admin/events'
    const es = new EventSource(url)
    es.addEventListener('health', (e) => {
      try { setEvent(JSON.parse(e.data)) } catch { /* ignore */ }
    })
    es.onerror = () => {
      setTimeout(() => {
        if (ref.current === es) {
          const k = localStorage.getItem('nk') || ''
          const u = k ? `/admin/events?token=${encodeURIComponent(k)}` : '/admin/events'
          ref.current = new EventSource(u)
        }
      }, 5000)
    }
    ref.current = es
    return () => { es.close(); ref.current = null }
  }, [])

  return event
}
