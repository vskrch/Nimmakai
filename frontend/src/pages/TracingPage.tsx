import { useState } from 'react'
import { Card, CardHeader, CardBody, Badge, Button, Input, Spinner, StatBox } from '../components/ui'
import { useStats } from '../hooks/useApi'
import { api } from '../lib/api'

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

  if (!stats) return <Spinner />

  const routing = stats.routing
  const catalog = stats.catalog

  return (
    <div className="animate-[fadeIn_0.3s_ease]">
      <div className="flex items-center gap-3 mb-6">
        <h2 className="text-xl font-semibold">Token Usage</h2>
        <Button size="sm" onClick={reload}>Refresh</Button>
      </div>

      <div className="grid grid-cols-[repeat(auto-fit,minmax(200px,1fr))] gap-4 mb-8">
        <StatBox label="Live Models" value={catalog?.live_model_count ?? 0} sub={`v${catalog?.yaml_version || '?'}`} />
        <StatBox label="Fallback Advances" value={routing?.fallback_advances ?? 0} sub="route quality signal" />
        <StatBox label="Total Intents" value={Object.values(routing?.intents_total || {}).reduce((a, b) => a + b, 0)} sub="across all types" />
        <StatBox label="Unique Models" value={Object.keys(routing?.models_total || {}).length} sub="used this session" />
      </div>

      {routing?.model_tokens && Object.keys(routing.model_tokens).length > 0 && (
        <Card>
          <CardHeader><h3 className="text-sm font-semibold">Usage by Model</h3></CardHeader>
          <CardBody className="p-0">
            <table className="w-full">
              <thead>
                <tr className="text-left border-b border-white/[0.08]">
                  <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px]">Model</th>
                  <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px]">Requests</th>
                  <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px]">Prompt Tokens</th>
                  <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px]">Completion Tokens</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(routing.model_tokens).sort((a, b) => (b[1].prompt_tokens + b[1].completion_tokens) - (a[1].prompt_tokens + a[1].completion_tokens)).map(([mid, tok]) => (
                  <tr key={mid} className="border-b border-white/[0.08] last:border-0 hover:bg-white/[0.01]">
                    <td className="px-6 py-3 text-[13px]">{mid.split('/').pop()}</td>
                    <td className="px-6 py-3 text-[13px] text-zinc-400">{routing.models_total?.[mid] ?? 0}</td>
                    <td className="px-6 py-3 text-[13px] text-zinc-400">{tok.prompt_tokens.toLocaleString()}</td>
                    <td className="px-6 py-3 text-[13px] text-zinc-400">{tok.completion_tokens.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardBody>
        </Card>
      )}

      {routing?.key_tokens && Object.keys(routing.key_tokens).length > 0 && (
        <Card>
          <CardHeader><h3 className="text-sm font-semibold">Usage by API Key</h3></CardHeader>
          <CardBody className="p-0">
            <table className="w-full">
              <thead>
                <tr className="text-left border-b border-white/[0.08]">
                  <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px]">Key ID</th>
                  <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px]">Prompt Tokens</th>
                  <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px]">Completion Tokens</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(routing.key_tokens).sort((a, b) => (b[1].prompt_tokens + b[1].completion_tokens) - (a[1].prompt_tokens + a[1].completion_tokens)).map(([kid, tok]) => (
                  <tr key={kid} className="border-b border-white/[0.08] last:border-0 hover:bg-white/[0.01]">
                    <td className="px-6 py-3 text-[13px] font-mono">{kid.slice(0, 12)}...</td>
                    <td className="px-6 py-3 text-[13px] text-zinc-400">{tok.prompt_tokens.toLocaleString()}</td>
                    <td className="px-6 py-3 text-[13px] text-zinc-400">{tok.completion_tokens.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardBody>
        </Card>
      )}

      <Card>
        <CardHeader><h3 className="text-sm font-semibold">Request Trace Lookup</h3></CardHeader>
        <CardBody>
          <div className="flex gap-2 mb-4">
            <Input
              placeholder="Request ID (X-Request-Id header)"
              value={traceId}
              onChange={e => setTraceId(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') lookupTrace() }}
              className="max-w-[300px]"
            />
            <Button onClick={lookupTrace} disabled={traceLoading}>
              {traceLoading ? 'Looking...' : 'Lookup'}
            </Button>
          </div>
          {traceResults && (
            <div className="border border-white/[0.08] rounded-lg overflow-hidden">
              {traceResults.length === 0 ? (
                <div className="p-4 text-center text-zinc-400 text-sm">No entries found for this request ID.</div>
              ) : (
                traceResults.map((e, i) => (
                  <div key={i} className="px-4 py-2 border-b border-white/[0.08] last:border-0 text-xs font-mono flex gap-3">
                    <span className="text-zinc-500 shrink-0">{e.time || ''}</span>
                    <span className={e.status && e.status >= 400 ? 'text-red-400' : 'text-emerald-400'}>{e.status || '—'}</span>
                    <span className="text-zinc-300">{e.message || JSON.stringify(e)}</span>
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
