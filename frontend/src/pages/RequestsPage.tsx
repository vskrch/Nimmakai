import { useState } from 'react'
import { Card, CardBody, CardHeader, Badge, Button, Input, Spinner, StatusDot } from '../components/ui'
import { useTraces, useTraceDetail } from '../hooks/useAnalytics'
import { RangePicker } from '../components/RangePicker'
import { fmtMs, fmtTokens, fmtUsd, fmtTime, rangeSince, qs } from '../lib/format'
import type { TraceSpan } from '../types/analytics'

function Waterfall({ spans }: { spans: TraceSpan[] }) {
  if (!spans.length) return <div className="text-sm text-zinc-500">No spans recorded</div>
  const t0 = Math.min(...spans.map(s => s.started_at))
  const t1 = Math.max(...spans.map(s => s.ended_at ?? s.started_at + (s.duration_ms || 0) / 1000))
  const total = Math.max(0.001, t1 - t0)
  const colors: Record<string, string> = {
    classify: 'bg-cyan-500/70',
    route: 'bg-violet-500/70',
    upstream: 'bg-emerald-500/70',
    fallback_advance: 'bg-amber-500/70',
  }
  return (
    <div className="flex flex-col gap-2">
      {spans.map((s, i) => {
        const left = ((s.started_at - t0) / total) * 100
        const width = Math.max(1.5, ((s.duration_ms || 0) / 1000 / total) * 100)
        const ok = s.success !== false
        return (
          <div key={i} className="text-xs">
            <div className="flex items-center gap-2 mb-1">
              <StatusDot ok={ok} />
              <span className="font-mono text-zinc-300 w-28 shrink-0">{s.span_type}</span>
              <span className="text-zinc-500 truncate flex-1">
                {s.model_id || s.metadata?.intent as string || s.error_message || ''}
              </span>
              <span className="tabular-nums text-zinc-400">{fmtMs(s.duration_ms)}</span>
            </div>
            <div className="h-3 bg-white/[0.03] rounded relative overflow-hidden">
              <div
                className={`absolute top-0 h-full rounded ${ok ? (colors[s.span_type] || 'bg-zinc-500') : 'bg-red-500/80'}`}
                style={{ left: `${left}%`, width: `${width}%` }}
              />
            </div>
          </div>
        )
      })}
    </div>
  )
}

export default function RequestsPage() {
  const [range, setRange] = useState('1h')
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('')
  const [intent, setIntent] = useState('')
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<string | null>(null)

  const filters = {
    since: rangeSince(range),
    limit: 40,
    offset,
    search: search || undefined,
    status: status || undefined,
    intent: intent || undefined,
  }
  const { data, loading, reload } = useTraces(filters)
  const { detail, loading: detailLoading } = useTraceDetail(selected)

  async function exportCsv() {
    const key = localStorage.getItem('nk') || ''
    const url = `/analytics/export/traces${qs({ format: 'csv', since: rangeSince(range), limit: 5000 })}`
    try {
      const res = await fetch(url, {
        credentials: 'include',
        headers: key ? { Authorization: `Bearer ${key}` } : {},
      })
      if (!res.ok) {
        console.error('CSV export failed', res.status)
        return
      }
      const blob = await res.blob()
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = `traces-${Date.now()}.csv`
      a.click()
      URL.revokeObjectURL(a.href)
    } catch (e) {
      console.error('CSV export error', e)
    }
  }

  return (
    <div className="animate-[fadeIn_0.3s_ease]">
      <div className="flex items-center justify-between mb-6 gap-3 flex-wrap">
        <h2 className="text-xl font-semibold">Request Explorer</h2>
        <div className="flex items-center gap-2 flex-wrap">
          <RangePicker value={range} onChange={v => { setRange(v); setOffset(0) }} />
          <Button size="sm" onClick={reload}>Refresh</Button>
          <Button size="sm" onClick={exportCsv}>Export CSV</Button>
        </div>
      </div>

      <div className="flex gap-2 mb-4 flex-wrap">
        <Input
          placeholder="Search trace / model / error"
          value={search}
          onChange={e => { setSearch(e.target.value); setOffset(0) }}
          className="max-w-xs"
        />
        <select
          value={status}
          onChange={e => { setStatus(e.target.value); setOffset(0) }}
          className="bg-black/20 border border-white/[0.08] text-white px-3 py-2 rounded-lg text-[13px]"
        >
          <option value="">All status</option>
          <option value="success">Success</option>
          <option value="error">Error</option>
          <option value="4xx">4xx</option>
          <option value="5xx">5xx</option>
        </select>
        <Input
          placeholder="Intent filter"
          value={intent}
          onChange={e => { setIntent(e.target.value); setOffset(0) }}
          className="max-w-[180px]"
        />
      </div>

      <Card>
        <CardBody className="p-0">
          {loading && !data ? <Spinner /> : (
            <table className="w-full">
              <thead>
                <tr className="text-left border-b border-white/[0.08]">
                  {['Time', 'Model', 'Intent', 'Tokens', 'Latency', 'Status'].map(h => (
                    <th key={h} className="px-4 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px]">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(data?.traces || []).map(t => (
                  <tr
                    key={t.trace_id}
                    onClick={() => setSelected(t.trace_id)}
                    className={`border-b border-white/[0.06] cursor-pointer hover:bg-white/[0.02] ${selected === t.trace_id ? 'bg-violet-500/10' : ''}`}
                  >
                    <td className="px-4 py-2.5 text-[12px] text-zinc-400 font-mono">{fmtTime(t.created_at)}</td>
                    <td className="px-4 py-2.5 text-[13px]">{(t.model_routed || '—').split('/').pop()}</td>
                    <td className="px-4 py-2.5 text-[12px] text-zinc-400">{t.intent || '—'}</td>
                    <td className="px-4 py-2.5 text-[12px] tabular-nums text-zinc-400">{fmtTokens(t.total_tokens)}</td>
                    <td className="px-4 py-2.5 text-[12px] tabular-nums">{fmtMs(t.duration_ms)}</td>
                    <td className="px-4 py-2.5">
                      <Badge variant={t.success ? 'ok' : 'err'}>
                        <StatusDot ok={!!t.success} />
                        {t.status_code ?? '—'}
                        {(t.fallback_index ?? 0) > 0 ? ' ⚠' : ''}
                      </Badge>
                    </td>
                  </tr>
                ))}
                {!data?.traces?.length && (
                  <tr><td colSpan={6} className="px-4 py-8 text-center text-zinc-500 text-sm">No traces in this range</td></tr>
                )}
              </tbody>
            </table>
          )}
        </CardBody>
      </Card>

      <div className="flex items-center justify-between mb-6 text-xs text-zinc-500">
        <span>{data?.total ?? 0} total</span>
        <div className="flex gap-2">
          <Button size="sm" disabled={offset <= 0} onClick={() => setOffset(Math.max(0, offset - 40))}>Prev</Button>
          <Button size="sm" disabled={!data || offset + 40 >= data.total} onClick={() => setOffset(offset + 40)}>Next</Button>
        </div>
      </div>

      {selected && (
        <Card>
          <CardHeader>
            <h3 className="text-sm font-semibold font-mono">{selected}</h3>
            <Button size="sm" onClick={() => navigator.clipboard.writeText(selected)}>Copy ID</Button>
          </CardHeader>
          <CardBody>
            {detailLoading || !detail ? <Spinner /> : (
              <div className="flex flex-col gap-4">
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-[13px]">
                  <div><span className="text-zinc-500">Provider</span><div>{detail.provider_id || '—'}</div></div>
                  <div><span className="text-zinc-500">Confidence</span><div>{detail.intent_confidence?.toFixed(2) ?? '—'}</div></div>
                  <div><span className="text-zinc-500">Cost</span><div>{fmtUsd(detail.estimated_cost_usd)}</div></div>
                  <div><span className="text-zinc-500">TTFT</span><div>{fmtMs(detail.upstream_ttft_ms)}</div></div>
                </div>
                {detail.chain && detail.chain.length > 0 && (
                  <div className="text-[12px] text-zinc-400">
                    Chain: <span className="text-zinc-300 font-mono">{detail.chain.join(' → ')}</span>
                    {detail.fallback_index ? ` (used index ${detail.fallback_index})` : ''}
                  </div>
                )}
                {detail.error_message && (
                  <div className="text-red-400 text-[13px]">{detail.error_message}</div>
                )}
                <h4 className="text-xs uppercase tracking-wider text-zinc-500">Span Waterfall</h4>
                <Waterfall spans={detail.spans || []} />
              </div>
            )}
          </CardBody>
        </Card>
      )}
    </div>
  )
}
