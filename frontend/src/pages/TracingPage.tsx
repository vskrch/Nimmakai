import { useState } from 'react'
import { Card, CardHeader, CardBody, Badge, Button, Input, StatBox, Skeleton, ErrorState, EmptyState } from '../components/ui'
import { useStats } from '../hooks/useApi'
import { api } from '../lib/api'
import { fmtNum } from '../lib/format'
import { Activity, BarChart3, KeyRound, RefreshCw, Search } from 'lucide-react'

interface TraceEntry {
  time?: string
  status?: number
  message?: string
  [key: string]: unknown
}

export default function TracingPage() {
  const { data: stats, reload } = useStats()
  const [traceId, setTraceId] = useState('')
  const [traceResults, setTraceResults] = useState<TraceEntry[] | null>(null)
  const [traceLoading, setTraceLoading] = useState(false)

  async function lookupTrace() {
    if (!traceId.trim()) return
    setTraceLoading(true)
    const r = await api<{ entries?: TraceEntry[] }>(`/admin/trace/${encodeURIComponent(traceId.trim())}`)
    setTraceLoading(false)
    if (r) setTraceResults(r.entries || [])
  }

  if (!stats) return (
    <div className="space-y-6 animate-[fadeIn_0.25s_ease-out]">
      <div className="flex items-center gap-2">
        <Activity className="w-5 h-5 text-violet-400" />
        <h2 className="text-lg font-bold text-white tracking-tight">Token Usage & Tracing</h2>
      </div>
      <Skeleton cards={4} />
      <Skeleton lines={8} />
    </div>
  )

  const routing = stats.routing
  const catalog = stats.catalog

  return (
    <div className="animate-[fadeIn_0.3s_ease]">
      <div className="flex items-center gap-3 mb-6">
        <Activity className="w-5 h-5 text-violet-400" />
        <h2 className="text-lg font-bold text-white tracking-tight">Token Usage &amp; Tracing</h2>
        <Button size="sm" variant="secondary" onClick={reload}>
          <RefreshCw className="w-3.5 h-3.5" />
          <span>Refresh</span>
        </Button>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <StatBox label="Live Models" value={catalog?.live_model_count ?? 0} sub={`v${catalog?.yaml_version || '?'}`} />
        <StatBox label="Fallback Advances" value={routing?.fallback_advances ?? 0} sub="route quality signal" />
        <StatBox label="Total Intents" value={Object.values(routing?.intents_total || {}).reduce((a, b) => a + b, 0)} sub="across all types" />
        <StatBox label="Unique Models" value={Object.keys(routing?.models_total || {}).length} sub="used this session" />
      </div>

      {routing?.model_tokens && Object.keys(routing.model_tokens).length > 0 ? (
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <BarChart3 className="w-4 h-4 text-violet-400" />
              <h3 className="text-sm font-semibold">Usage by Model</h3>
            </div>
          </CardHeader>
          <CardBody className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs min-w-[520px]">
                <thead>
                  <tr className="border-b border-white/[0.08] text-[10px] uppercase tracking-wider text-zinc-400 bg-white/[0.01]">
                    <th className="px-4 sm:px-6 py-3.5 font-semibold">Model</th>
                    <th className="px-4 sm:px-6 py-3.5 font-semibold">Requests</th>
                    <th className="px-4 sm:px-6 py-3.5 font-semibold">Prompt Tokens</th>
                    <th className="px-4 sm:px-6 py-3.5 font-semibold">Completion Tokens</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/[0.06]">
                  {Object.entries(routing.model_tokens).sort((a, b) => (b[1].prompt_tokens + b[1].completion_tokens) - (a[1].prompt_tokens + a[1].completion_tokens)).map(([mid, tok]) => (
                    <tr key={mid} className="hover:bg-white/[0.02] transition-colors">
                      <td className="px-4 sm:px-6 py-4 font-semibold text-white">{mid.split('/').pop()}</td>
                      <td className="px-4 sm:px-6 py-4 text-zinc-300 font-mono">{routing.models_total?.[mid] ?? 0}</td>
                    <td className="px-4 sm:px-6 py-4 text-zinc-300 font-mono">{fmtNum(tok.prompt_tokens)}</td>
                    <td className="px-4 sm:px-6 py-4 text-zinc-300 font-mono">{fmtNum(tok.completion_tokens)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardBody>
        </Card>
      ) : (
        <EmptyState title="No model token data" icon={BarChart3}>Usage will appear once requests are processed.</EmptyState>
      )}

      {routing?.key_tokens && Object.keys(routing.key_tokens).length > 0 ? (
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <KeyRound className="w-4 h-4 text-violet-400" />
              <h3 className="text-sm font-semibold">Usage by API Key</h3>
            </div>
          </CardHeader>
          <CardBody className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs min-w-[420px]">
                <thead>
                  <tr className="border-b border-white/[0.08] text-[10px] uppercase tracking-wider text-zinc-400 bg-white/[0.01]">
                    <th className="px-4 sm:px-6 py-3.5 font-semibold">Key ID</th>
                    <th className="px-4 sm:px-6 py-3.5 font-semibold">Prompt Tokens</th>
                    <th className="px-4 sm:px-6 py-3.5 font-semibold">Completion Tokens</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/[0.06]">
                  {Object.entries(routing.key_tokens).sort((a, b) => (b[1].prompt_tokens + b[1].completion_tokens) - (a[1].prompt_tokens + a[1].completion_tokens)).map(([kid, tok]) => (
                    <tr key={kid} className="hover:bg-white/[0.02] transition-colors">
                      <td className="px-4 sm:px-6 py-4 font-mono text-white">{kid.slice(0, 12)}…</td>
                      <td className="px-4 sm:px-6 py-4 text-zinc-300 font-mono">{fmtNum(tok.prompt_tokens)}</td>
                      <td className="px-4 sm:px-6 py-4 text-zinc-300 font-mono">{fmtNum(tok.completion_tokens)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardBody>
        </Card>
      ) : (
        <EmptyState title="No key token data" icon={KeyRound}>Key usage will appear once requests are processed.</EmptyState>
      )}

      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Search className="w-4 h-4 text-violet-400" />
            <h3 className="text-sm font-semibold">Request Trace Lookup</h3>
          </div>
        </CardHeader>
        <CardBody>
          <div className="flex gap-2 mb-4 flex-wrap">
            <Input
              placeholder="Request ID (X-Request-Id header)"
              value={traceId}
              onChange={e => setTraceId(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') lookupTrace() }}
              className="max-w-[300px]"
            />
            <Button onClick={lookupTrace} disabled={traceLoading} variant="secondary">
              {traceLoading ? 'Looking...' : 'Lookup'}
            </Button>
          </div>
          {traceResults && (
            <div className="border border-white/[0.08] rounded-xl overflow-hidden">
              {traceResults.length === 0 ? (
                <div className="p-6 text-center text-zinc-500 text-xs">No entries found for this request ID.</div>
              ) : (
                traceResults.map((e, i) => (
                  <div key={i} className="px-4 py-2.5 border-b border-white/[0.08] last:border-0 text-xs font-mono flex gap-3">
                    <span className="text-zinc-500 shrink-0">{e.time || ''}</span>
                    <span className={e.status && e.status >= 400 ? 'text-rose-400' : 'text-emerald-400'}>{e.status || '—'}</span>
                    <span className="text-zinc-300 break-all">{e.message || JSON.stringify(e)}</span>
                  </div>
                ))
              )}
            </div>
          )}
        </CardBody>
      </Card>
    </div>
  )
}
