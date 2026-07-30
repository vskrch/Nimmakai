import React, { useState } from 'react'
import { ap, errMsg, okBody, setAuthKey } from '../lib/api'
import { Button, Card, CardHeader, CardBody, Badge, StatusDot, CopyButton, CodeBlock, Skeleton } from '../components/ui'
import type { AuthSession } from '../components/AuthModal'
import {
  User,
  Key,
  RefreshCw,
  ShieldCheck,
  CheckCircle2,
  AlertCircle,
  Terminal,
  Link as LinkIcon,
  Globe
} from 'lucide-react'

interface AccountPageProps {
  session: AuthSession | null
  onRefresh: () => Promise<void>
}

export default function AccountPage({ session, onRefresh }: AccountPageProps) {
  const [msg, setMsg] = useState('')
  const [newKey, setNewKey] = useState<string | null>(null)
  const user = session?.user
  const keys = session?.keys || []
  const conn = session?.connection
  const baseUrl = conn?.base_url || ''
  const eps = conn?.endpoints || {}

  async function rotate() {
    setMsg('')
    setNewKey(null)
    const r = await ap<{ api_key?: string; message?: string }>('/auth/keys/rotate', {})
    if (!okBody(r)) {
      setMsg(errMsg(r, 'Key rotation failed'))
      return
    }
    if (r?.api_key) {
      setNewKey(r.api_key)
      setAuthKey(r.api_key)
    }
    setMsg(r?.message || 'API key rotated successfully')
    await onRefresh()
  }

  async function resendEmail() {
    setMsg('')
    const r = await ap<{ message?: string; verify_url?: string }>('/auth/resend-verification', {})
    if (!okBody(r)) {
      setMsg(errMsg(r, 'Failed to resend email'))
      return
    }
    setMsg(r?.message || 'Verification link sent. Check your email.')
  }

  const chatUrl = eps.openai_chat_completions || (baseUrl ? `${baseUrl}/v1/chat/completions` : '/v1/chat/completions')
  const sampleCurl = `curl -X POST ${chatUrl} \\
  -H "Authorization: Bearer ${newKey || 'YOUR_POTATO_API_KEY'}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "auto",
    "messages": [{"role": "user", "content": "Explain quantum computing simply"}]
  }'`

  const endpointRows: Array<{ label: string; value: string; hint?: string }> = baseUrl
    ? [
        { label: 'Gateway Base URL', value: baseUrl, hint: 'OpenAI-compatible root' },
        { label: 'Chat Completions', value: eps.openai_chat_completions || `${baseUrl}/v1/chat/completions`, hint: 'POST' },
        { label: 'Completions', value: eps.openai_completions || `${baseUrl}/v1/completions`, hint: 'POST' },
        { label: 'Embeddings', value: eps.openai_embeddings || `${baseUrl}/v1/embeddings`, hint: 'POST' },
        { label: 'List Models', value: eps.openai_models || `${baseUrl}/v1/models`, hint: 'GET' },
        { label: 'Anthropic Messages', value: eps.anthropic_messages || `${baseUrl}/v1/messages`, hint: 'POST' },
        { label: 'Analytics Traces', value: eps.analytics_traces || `${baseUrl}/analytics/traces`, hint: 'GET' },
        { label: 'Health', value: eps.health || `${baseUrl}/health`, hint: 'GET' }
      ]
    : []

  return (
    <div className="space-y-6 max-w-2xl animate-[fadeIn_0.25s_ease-out]">
      {!user ? (
        <>
          <div className="flex items-center gap-2">
            <User className="w-5 h-5 text-violet-400" />
            <h2 className="text-lg font-bold text-white tracking-tight">User Account &amp; API Tokens</h2>
          </div>
          <Skeleton cards={3} />
        </>
      ) : (
        <>
          {/* Header */}
          <div>
            <div className="flex items-center gap-2">
              <User className="w-5 h-5 text-violet-400" />
              <h2 className="text-lg font-bold text-white tracking-tight">User Account &amp; API Tokens</h2>
            </div>
            <p className="text-zinc-400 text-xs mt-1">Manage profile parameters, authentication status, and access keys.</p>
          </div>

          {/* Account Info Card */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-violet-400" />
            <h3 className="text-sm font-semibold text-white">Profile Credentials</h3>
          </div>
          <Badge variant={user?.status === 'active' ? 'ok' : 'warn'}>
            <StatusDot ok={user?.status === 'active'} />
            {user?.status || 'Unknown'}
          </Badge>
        </CardHeader>
        <CardBody className="space-y-3 text-xs">
          <div className="flex justify-between items-center py-2 border-b border-white/[0.06]">
            <span className="text-zinc-400">Account Email Address</span>
            <span className="font-semibold text-white">{user?.email || '—'}</span>
          </div>
          <div className="flex justify-between items-center py-2 border-b border-white/[0.06]">
            <span className="text-zinc-400">Account Status</span>
            <span className="capitalize font-semibold text-emerald-400">{user?.status || '—'}</span>
          </div>
          <div className="flex justify-between items-center py-2">
            <span className="text-zinc-400">Platform Access Role</span>
            <Badge variant="purple" className="capitalize">{user?.role || 'User'}</Badge>
          </div>
        </CardBody>
      </Card>

      {/* Status Warning Banners */}
      {user?.status === 'pending_approval' && (
        <div className="rounded-2xl border border-amber-500/30 bg-amber-500/10 p-5 text-xs text-amber-200 flex items-center gap-3">
          <AlertCircle className="w-5 h-5 text-amber-400 shrink-0" />
          <div>
            <strong className="font-semibold text-amber-100">Verification Complete: </strong>
            Your email is confirmed. Account is currently queued for administrator approval before API keys are active.
          </div>
        </div>
      )}
      {user?.status === 'unverified' && (
        <div className="rounded-2xl border border-sky-500/30 bg-sky-500/10 p-5 text-xs text-sky-200 flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <AlertCircle className="w-5 h-5 text-sky-400 shrink-0" />
            <div>
              <strong className="font-semibold text-sky-100">Email Unverified: </strong>
              Please check your inbox for the verification link to activate your access.
            </div>
          </div>
          <Button size="xs" variant="primary" onClick={resendEmail}>
            <span>Resend Link</span>
          </Button>
        </div>
      )}

      {/* API Key Management */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Key className="w-4 h-4 text-violet-400" />
            <h3 className="text-sm font-semibold text-white">Potato Gateway API Tokens</h3>
          </div>
          {user?.status === 'active' && (
            <Button size="xs" variant="primary" onClick={rotate}>
              <RefreshCw className="w-3 h-3" />
              <span>Rotate Token</span>
            </Button>
          )}
        </CardHeader>
        <CardBody className="space-y-4 text-xs">
          {keys.length === 0 ? (
            <p className="text-zinc-500 py-4 text-center">No API tokens issued yet. Tokens are automatically generated upon account approval.</p>
          ) : (
            <div className="space-y-2">
              {keys.map(k => (
                <div key={k.key_prefix + String(k.revoked_at)} className="flex items-center justify-between p-3 rounded-xl bg-zinc-950/60 border border-white/[0.06]">
                  <div className="flex items-center gap-2">
                    <Key className="w-3.5 h-3.5 text-violet-400" />
                    <code className="font-mono text-zinc-200 font-semibold">{k.key_prefix}••••••••</code>
                  </div>
                  <Badge variant={k.revoked_at ? 'err' : 'ok'}>
                    {k.revoked_at ? 'Revoked' : 'Active Token'}
                  </Badge>
                </div>
              ))}
            </div>
          )}

          {newKey && (
            <div className="p-4 rounded-2xl border border-amber-500/30 bg-amber-500/10 space-y-2">
              <div className="flex items-center justify-between">
                <span className="font-semibold text-amber-200">Newly Rotated API Key (Saved to session):</span>
                <CopyButton text={newKey} />
              </div>
              <code className="block font-mono text-amber-100 text-xs font-bold break-all bg-black/40 p-3 rounded-xl border border-amber-500/20">
                {newKey}
              </code>
            </div>
          )}

          {msg && <p className="text-xs text-emerald-400 font-medium">{msg}</p>}
        </CardBody>
      </Card>

      {/* Code Snippet integration example */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Terminal className="w-4 h-4 text-violet-400" />
            <h3 className="text-sm font-semibold text-white">cURL Integration Example</h3>
          </div>
        </CardHeader>
        <CardBody>
          <CodeBlock code={sampleCurl} language="bash" />
        </CardBody>
      </Card>

      {/* Connection Endpoints */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Globe className="w-4 h-4 text-violet-400" />
            <h3 className="text-sm font-semibold text-white">Connection Endpoints</h3>
          </div>
          <Badge variant="default">{baseUrl || '—'}</Badge>
        </CardHeader>
        <CardBody className="space-y-2 text-xs">
          {endpointRows.length === 0 ? (
            <p className="text-zinc-500 py-2 text-center">Connection details unavailable until signed in.</p>
          ) : (
            <div className="space-y-1.5">
              {endpointRows.map(row => (
                <div
                  key={row.label}
                  className="flex items-center justify-between gap-3 p-2.5 rounded-xl bg-zinc-950/60 border border-white/[0.06]"
                >
                  <div className="flex items-center gap-2 min-w-0">
                    <LinkIcon className="w-3.5 h-3.5 text-zinc-500 shrink-0" />
                    <div className="min-w-0">
                      <div className="text-zinc-300 font-semibold flex items-center gap-1.5">
                        {row.label}
                        {row.hint && (
                          <span className="text-[9px] uppercase tracking-wider text-zinc-500 bg-white/[0.04] px-1.5 py-0.5 rounded">
                            {row.hint}
                          </span>
                        )}
                      </div>
                      <code className="block font-mono text-[11px] text-emerald-400/90 truncate">{row.value}</code>
                    </div>
                  </div>
                  <CopyButton text={row.value} />
                </div>
              ))}
              <p className="text-[10px] text-zinc-500 pt-1">
                Use <code className="font-mono text-zinc-400">Authorization: Bearer &lt;your-api-key&gt;</code> for all
                endpoints. The OpenAI SDK can be pointed at <code className="font-mono text-zinc-400">{baseUrl}/v1</code> as
                the <code className="font-mono text-zinc-400">base_url</code>.
              </p>
            </div>
          )}
        </CardBody>
      </Card>
    </>
    )}
  </div>
)
}
