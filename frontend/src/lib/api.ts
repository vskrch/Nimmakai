export function getAuthKey(): string {
  return localStorage.getItem('nk') || ''
}

export function setAuthKey(key: string) {
  localStorage.setItem('nk', key)
}

export function clearAuthKey() {
  localStorage.removeItem('nk')
}

function headers(extra?: Record<string, string>): Record<string, string> {
  const h: Record<string, string> = { 'Content-Type': 'application/json', ...extra }
  const key = getAuthKey()
  if (key) h['Authorization'] = `Bearer ${key}`
  return h
}

export async function api<T = unknown>(path: string, opts?: RequestInit): Promise<T | null> {
  try {
    const res = await fetch(path, {
      credentials: 'include',
      ...opts,
      headers: { ...headers(), ...(opts?.headers as Record<string, string> | undefined) },
    })
    const ct = res.headers.get('content-type') || ''
    let body: unknown = null
    if (ct.includes('application/json')) {
      try { body = await res.json() } catch { body = null }
    } else {
      const text = await res.text()
      try { body = JSON.parse(text) } catch { body = { message: text } }
    }
    if (res.status === 401) {
      clearAuthKey()
      return null
    }
    if (!res.ok) {
      const b = (body || {}) as Record<string, unknown>
      b.__http_status = res.status
      b.__ok = false
      return b as T
    }
    return body as T
  } catch (e) {
    console.error('API error:', e)
    return null
  }
}

export async function ap<T = unknown>(path: string, body: unknown): Promise<T | null> {
  return api<T>(path, { method: 'POST', body: JSON.stringify(body) })
}

export async function ad<T = unknown>(path: string): Promise<T | null> {
  return api<T>(path, { method: 'DELETE' })
}

export function errMsg(body: unknown, fallback = 'Request failed'): string {
  if (!body) return fallback
  if (typeof body === 'string') return body
  const b = body as Record<string, unknown>
  if (b.error && typeof b.error === 'object' && 'message' in b.error) return String((b.error as Record<string, unknown>).message)
  if (b.detail && typeof b.detail === 'object' && 'error' in b.detail) {
    const inner = (b.detail as Record<string, unknown>).error as Record<string, unknown>
    if (inner?.message) return String(inner.message)
  }
  if (typeof b.detail === 'string') return b.detail
  if (b.message) return String(b.message)
  return fallback
}

/** True when `api()` returned a successful JSON body (not an error envelope). */
export function okBody(r: unknown): boolean {
  if (!r || typeof r !== 'object') return false
  const o = r as Record<string, unknown>
  return o.__ok !== false && !o.error
}
