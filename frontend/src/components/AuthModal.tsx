import { useState } from 'react'
import { Button, Input } from './ui'

interface AuthModalProps {
  onAuth: (key: string) => Promise<boolean>
}

export default function AuthModal({ onAuth }: AuthModalProps) {
  const [key, setKey] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit() {
    if (!key.trim()) { setError('Enter your PROXY_API_KEYS value'); return }
    setLoading(true)
    setError('')
    const ok = await onAuth(key.trim())
    setLoading(false)
    if (!ok) setError('Invalid API key — check PROXY_API_KEYS')
  }

  return (
    <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center">
      <div className="bg-zinc-900 border border-white/[0.08] rounded-2xl w-[400px] max-w-[90vw] shadow-[0_32px_64px_rgba(0,0,0,0.5)] animate-[fadeIn_0.2s_ease]">
        <div className="px-6 py-5 border-b border-white/[0.08]">
          <h3 className="font-semibold">Admin Access</h3>
        </div>
        <div className="p-6 flex flex-col gap-4">
          <div>
            <label className="block text-xs text-zinc-400 mb-1.5">Gateway Proxy Key</label>
            <Input
              type="password"
              placeholder="sk-..."
              value={key}
              onChange={e => setKey(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') handleSubmit() }}
            />
          </div>
          {error && <p className="text-red-400 text-xs">{error}</p>}
          <p className="text-xs text-zinc-500">Authenticates admin APIs and playground requests.</p>
        </div>
        <div className="px-6 py-4 border-t border-white/[0.08] flex justify-end bg-black/20 rounded-b-2xl">
          <Button variant="primary" onClick={handleSubmit} disabled={loading}>
            {loading ? 'Connecting...' : 'Connect'}
          </Button>
        </div>
      </div>
    </div>
  )
}
