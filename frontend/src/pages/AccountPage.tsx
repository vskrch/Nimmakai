import { useState } from 'react'
import { ap, errMsg, okBody, setAuthKey } from '../lib/api'
import { Button } from '../components/ui'
import type { AuthSession } from '../components/AuthModal'

interface AccountPageProps {
  session: AuthSession | null
  onRefresh: () => Promise<void>
}

export default function AccountPage({ session, onRefresh }: AccountPageProps) {
  const [msg, setMsg] = useState('')
  const [newKey, setNewKey] = useState<string | null>(null)
  const user = session?.user
  const keys = session?.keys || []

  async function rotate() {
    setMsg('')
    setNewKey(null)
    const r = await ap<{ api_key?: string; message?: string }>('/auth/keys/rotate', {})
    if (!okBody(r)) {
      setMsg(errMsg(r, 'Rotate failed'))
      return
    }
    if (r?.api_key) {
      setNewKey(r.api_key)
      setAuthKey(r.api_key)
    }
    setMsg(r?.message || 'Key rotated')
    await onRefresh()
  }

  return (
    <div className="space-y-6 max-w-xl">
      <div>
        <h3 className="text-lg font-semibold">Account</h3>
        <p className="text-sm text-zinc-500 mt-1">Your profile and API keys.</p>
      </div>

      <div className="rounded-xl border border-white/[0.08] p-5 space-y-3 text-sm">
        <div className="flex justify-between"><span className="text-zinc-500">Email</span><span>{user?.email || '—'}</span></div>
        <div className="flex justify-between"><span className="text-zinc-500">Status</span><span>{user?.status || '—'}</span></div>
        <div className="flex justify-between"><span className="text-zinc-500">Role</span><span>{user?.role || '—'}</span></div>
      </div>

      {user?.status === 'pending_approval' && (
        <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 text-sm text-amber-100">
          Email verified. Waiting for an admin to approve your account before an API key is issued.
        </div>
      )}
      {user?.status === 'unverified' && (
        <div className="rounded-xl border border-sky-500/30 bg-sky-500/10 p-4 text-sm text-sky-100">
          Check your email for a verification link (stub backend also returns it at signup).
        </div>
      )}

      <div className="rounded-xl border border-white/[0.08] p-5 space-y-3">
        <h4 className="font-medium">API keys</h4>
        {keys.length === 0 && (
          <p className="text-sm text-zinc-500">No keys yet — issued when an admin approves you.</p>
        )}
        {keys.map(k => (
          <div key={k.key_prefix + String(k.revoked_at)} className="text-sm flex justify-between">
            <code className="text-zinc-300">{k.key_prefix}</code>
            <span className="text-zinc-500">{k.revoked_at ? 'revoked' : 'active'}</span>
          </div>
        ))}
        {user?.status === 'active' && (
          <Button variant="primary" onClick={rotate}>Rotate key</Button>
        )}
        {newKey && (
          <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs break-all">
            <p className="mb-1 text-amber-200">New key (saved to this browser):</p>
            <code>{newKey}</code>
          </div>
        )}
        {msg && <p className="text-xs text-zinc-400">{msg}</p>}
      </div>
    </div>
  )
}
