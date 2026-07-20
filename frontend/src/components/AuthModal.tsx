import { useState } from 'react'
import { Button, Input } from './ui'
import { api, ap, errMsg, setAuthKey } from '../lib/api'

export type AuthSession = {
  authenticated: boolean
  is_admin?: boolean
  via?: string
  user?: {
    id: string | null
    email: string | null
    role: string
    status: string
  }
  keys?: Array<{ key_prefix: string; revoked_at: number | null }>
}

interface AuthModalProps {
  onSession: (session: AuthSession) => void
}

type Tab = 'signin' | 'signup' | 'key'

export default function AuthModal({ onSession }: AuthModalProps) {
  const [tab, setTab] = useState<Tab>('signin')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [key, setKey] = useState('')
  const [error, setError] = useState('')
  const [info, setInfo] = useState('')
  const [loading, setLoading] = useState(false)

  async function refreshMe() {
    const me = await api<AuthSession>('/auth/me')
    if (me?.authenticated) onSession(me)
    return me
  }

  async function handleSignIn() {
    setLoading(true)
    setError('')
    setInfo('')
    const r = await ap<{ ok?: boolean; error?: { message?: string } }>('/auth/login', {
      email,
      password,
    })
    setLoading(false)
    if (!r || (r as { __ok?: boolean }).__ok === false || r.error) {
      setError(errMsg(r, 'Invalid email or password'))
      return
    }
    await refreshMe()
  }

  async function handleSignUp() {
    setLoading(true)
    setError('')
    setInfo('')
    const r = await ap<{
      ok?: boolean
      verify_url?: string
      message?: string
      error?: { message?: string }
    }>('/auth/signup', { email, password })
    setLoading(false)
    if (!r || (r as { __ok?: boolean }).__ok === false || r.error) {
      setError(errMsg(r, 'Signup failed'))
      return
    }
    let msg = r.message || 'Check your email to verify.'
    if (r.verify_url) {
      msg += ` Dev link: ${r.verify_url}`
    }
    setInfo(msg)
    setTab('signin')
  }

  async function handleKey() {
    if (!key.trim()) {
      setError('Enter your API key or PROXY_API_KEYS value')
      return
    }
    setLoading(true)
    setError('')
    setAuthKey(key.trim())
    const r = await api('/stats')
    setLoading(false)
    if (!r || (r as { __ok?: boolean }).__ok === false) {
      setAuthKey('')
      setError('Invalid API key')
      return
    }
    const me = await refreshMe()
    if (!me?.authenticated) {
      onSession({
        authenticated: true,
        is_admin: true,
        via: 'legacy_proxy',
        user: { id: null, email: null, role: 'admin', status: 'active' },
      })
    }
  }

  return (
    <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center">
      <div className="bg-zinc-900 border border-white/[0.08] rounded-2xl w-[420px] max-w-[90vw] shadow-[0_32px_64px_rgba(0,0,0,0.5)]">
        <div className="px-6 py-5 border-b border-white/[0.08]">
          <h3 className="font-semibold">Nimmakai</h3>
          <p className="text-xs text-zinc-500 mt-1">Sign in to your account or use an API key</p>
        </div>

        <div className="px-6 pt-4 flex gap-2">
          {([
            ['signin', 'Sign in'],
            ['signup', 'Sign up'],
            ['key', 'API key'],
          ] as const).map(([id, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => { setTab(id); setError(''); setInfo('') }}
              className={
                tab === id
                  ? 'text-xs px-3 py-1.5 rounded-lg bg-violet-500/20 text-violet-200 border border-violet-500/30'
                  : 'text-xs px-3 py-1.5 rounded-lg text-zinc-400 border border-transparent hover:bg-white/[0.04]'
              }
            >
              {label}
            </button>
          ))}
        </div>

        <div className="p-6 flex flex-col gap-4">
          {tab !== 'key' && (
            <>
              <div>
                <label className="block text-xs text-zinc-400 mb-1.5">Email</label>
                <Input
                  type="email"
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  placeholder="you@example.com"
                />
              </div>
              <div>
                <label className="block text-xs text-zinc-400 mb-1.5">Password</label>
                <Input
                  type="password"
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter') tab === 'signin' ? handleSignIn() : handleSignUp()
                  }}
                  placeholder="••••••••"
                />
              </div>
            </>
          )}

          {tab === 'key' && (
            <div>
              <label className="block text-xs text-zinc-400 mb-1.5">API key</label>
              <Input
                type="password"
                placeholder="sk-nk-… or PROXY_API_KEYS"
                value={key}
                onChange={e => setKey(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') handleKey() }}
              />
              <p className="text-[11px] text-zinc-500 mt-2">
                User keys start with sk-nk-. Legacy PROXY_API_KEYS still work for admin break-glass.
              </p>
            </div>
          )}

          {error && <p className="text-red-400 text-xs">{error}</p>}
          {info && <p className="text-emerald-400/90 text-xs break-all">{info}</p>}
        </div>

        <div className="px-6 py-4 border-t border-white/[0.08] flex justify-end bg-black/20 rounded-b-2xl">
          <Button
            variant="primary"
            disabled={loading}
            onClick={() => {
              if (tab === 'signin') handleSignIn()
              else if (tab === 'signup') handleSignUp()
              else handleKey()
            }}
          >
            {loading ? 'Working…' : tab === 'signup' ? 'Create account' : 'Continue'}
          </Button>
        </div>
      </div>
    </div>
  )
}
