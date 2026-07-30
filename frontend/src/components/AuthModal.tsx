import React, { useState } from 'react'
import { Button, Input } from './ui'
import { api, ap, errMsg, setAuthKey } from '../lib/api'
import { Zap, Key, Lock, Mail, ArrowRight, ShieldCheck } from 'lucide-react'

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
  keys?: Array<{
    id?: string
    key_prefix: string
    name?: string
    created_at?: number
    revoked_at: number | null
    last_used_at?: number | null
  }>
  connection?: {
    base_url: string
    endpoints: Record<string, string>
  }
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
    <div className="fixed inset-0 bg-black/80 backdrop-blur-md z-50 flex items-center justify-center p-4">
      <div className="bg-zinc-900 border border-white/[0.12] rounded-3xl w-[440px] max-w-[95vw] shadow-[0_32px_80px_rgba(0,0,0,0.8)] overflow-hidden animate-[fadeIn_0.2s_ease-out]">
        {/* Header */}
        <div className="px-6 py-6 border-b border-white/[0.08] bg-zinc-950/40 text-center">
          <div className="w-12 h-12 bg-gradient-to-br from-violet-600 to-fuchsia-600 rounded-2xl flex items-center justify-center shadow-[0_0_24px_rgba(139,92,246,0.5)] mx-auto mb-3 border border-white/20">
            <Zap className="w-6 h-6 text-white fill-white" />
          </div>
          <h3 className="text-lg font-bold text-white tracking-tight">Potato Gateway</h3>
          <p className="text-xs text-zinc-400 mt-1">Authenticate to access the multi-provider LLM dashboard</p>
        </div>

        {/* Tabs */}
        <div className="px-6 pt-5 flex gap-1.5 border-b border-white/[0.06] bg-zinc-950/20">
          {([
            ['signin', 'Sign In'],
            ['signup', 'Create Account'],
            ['key', 'API Key Direct'],
          ] as const).map(([id, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => { setTab(id); setError(''); setInfo('') }}
              className={`flex-1 text-xs py-2.5 font-semibold rounded-t-xl transition-all border-b-2 ${
                tab === id
                  ? 'border-violet-500 text-violet-300 bg-violet-500/10'
                  : 'border-transparent text-zinc-400 hover:text-zinc-200 hover:bg-white/[0.02]'
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {/* Form Body */}
        <div className="p-6 space-y-4">
          {tab !== 'key' && (
            <>
              <div>
                <label className="block text-xs font-semibold text-zinc-300 mb-1.5">Email Address</label>
                <Input
                  type="email"
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  placeholder="you@example.com"
                />
              </div>
              <div>
                <label className="block text-xs font-semibold text-zinc-300 mb-1.5">Password</label>
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
              <label className="block text-xs font-semibold text-zinc-300 mb-1.5">API Key or Admin Bearer Token</label>
              <Input
                type="password"
                placeholder="sk-nk-… or PROXY_API_KEYS"
                value={key}
                onChange={e => setKey(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') handleKey() }}
              />
              <p className="text-[11px] text-zinc-400 mt-2 font-mono">
                Provide an active user API key starting with <code className="text-violet-300">sk-nk-</code> or environment bearer token.
              </p>
            </div>
          )}

          {error && (
            <div className="text-xs text-rose-300 bg-rose-500/10 border border-rose-500/20 p-3 rounded-xl">
              {error}
            </div>
          )}
          {info && (
            <div className="text-xs text-emerald-300 bg-emerald-500/10 border border-emerald-500/20 p-3 rounded-xl font-mono break-all">
              {info}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-white/[0.08] flex justify-end bg-zinc-950/60">
          <Button
            variant="primary"
            disabled={loading}
            onClick={() => {
              if (tab === 'signin') handleSignIn()
              else if (tab === 'signup') handleSignUp()
              else handleKey()
            }}
            className="w-full justify-center"
          >
            <span>{loading ? 'Authenticating…' : tab === 'signup' ? 'Create Gateway Account' : 'Authenticate Session'}</span>
            <ArrowRight className="w-4 h-4" />
          </Button>
        </div>
      </div>
    </div>
  )
}
