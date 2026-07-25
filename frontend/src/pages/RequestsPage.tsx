import React, { useState, useMemo, useEffect } from 'react'
import { Card, CardBody, CardHeader, Badge, Button, Input, Select, Spinner, StatusDot, CopyButton, CodeBlock } from '../components/ui'
import { useTraces, useTraceDetail } from '../hooks/useAnalytics'
import { RangePicker } from '../components/RangePicker'
import { fmtMs, fmtTokens, fmtUsd, fmtTime, rangeSince, qs } from '../lib/format'
import type { TraceSpan } from '../types/analytics'
import {
  ListFilter,
  Search,
  Download,
  RefreshCw,
  ChevronLeft,
  ChevronRight,
  Clock,
  Cpu,
  Coins,
  X,
  Zap,
  Activity
} from 'lucide-react'

function Waterfall({ spans }: { spans: TraceSpan[] }) {
  if (!spans.length) return <div className="text-xs text-zinc-500 py-4">No spans recorded for this execution trace</div>
  const t0 = Math.min(...spans.map(s => s.started_at))
  const t1 = Math.max(...spans.map(s => s.ended_at ?? s.started_at + (s.duration_ms || 0) / 1000))
  const total = Math.max(0.001, t1 - t0)

  const colors: Record<string, string> = {
    classify: 'bg-cyan-500/80',
    route: 'bg-violet-500/80',
    upstream: 'bg-emerald-500/80',
    fallback_advance: 'bg-amber-500/80',
  }

  return (
    <div className="space-y-3 font-sans">
      {spans.map((s, i) => {
        const left = ((s.started_at - t0) / total) * 100
        const width = Math.max(2.0, ((s.duration_ms || 0) / 1000 / total) * 100)
        const ok = s.success !== false

        return (
          <div key={i} className="text-xs space-y-1">
            <div className="flex items-center justify-between gap-2 text-[11px]">
              <div className="flex items-center gap-2 min-w-0">
                <StatusDot ok={ok} />
                <span className="font-mono font-semibold text-white uppercase">{s.span_type}</span>
                <span className="text-zinc-400 truncate max-w-[240px]">
                  {s.model_id || (s.metadata?.intent as string) || s.error_message || ''}
                </span>
              </div>
              <span className="font-mono text-zinc-300 font-medium shrink-0">{fmtMs(s.duration_ms)}</span>
            </div>
            <div className="h-3 bg-zinc-950 rounded-md relative overflow-hidden border border-white/[0.05]">
              <div
                className={`absolute top-0 h-full rounded ${ok ? (colors[s.span_type] || 'bg-zinc-500') : 'bg-rose-500/80'}`}
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
  const [searchDebounced, setSearchDebounced] = useState('')
  const [status, setStatus] = useState('')
  const [intent, setIntent] = useState('')
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<string | null>(null)
  const [exportError, setExportError] = useState<string | null>(null)

  useEffect(() => {
    const t = setTimeout(() => setSearchDebounced(search), 300)
    return () => clearTimeout(t)
  }, [search])

  const filters = useMemo(
    () => ({
      range,
      limit: 40,
      offset,
      search: searchDebounced || undefined,
      status: status || undefined,
      intent: intent || undefined,
    }),
    [range, offset, searchDebounced, status, intent],
  )
  const { data, loading, error, reload } = useTraces(filters)
  const { detail, loading: detailLoading, error: detailError } = useTraceDetail(selected)

  async function exportCsv() {
    setExportError(null)
    const key = localStorage.getItem('nk') || ''
    const url = `/analytics/export/traces${qs({ format: 'csv', since: rangeSince(range), limit: 5000 })}`
    try {
      const res = await fetch(url, {
        credentials: 'include',
        headers: key ? { Authorization: `Bearer ${key}` } : {},
      })
      if (!res.ok) {
        setExportError(`CSV export failed (${res.status})`)
        return
      }
      const blob = await res.blob()
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = `traces-${Date.now()}.csv`
      a.click()
      URL.revokeObjectURL(a.href)
    } catch (e) {
      setExportError(e instanceof Error ? e.message : 'CSV export failed')
    }
  }

  return (
    <div className="space-y-6 animate-[fadeIn_0.25s_ease-out]">
      {/* Header controls */}
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <ListFilter className="w-5 h-5 text-violet-400" />
          <h2 className="text-lg font-bold text-white tracking-tight">Request Explorer</h2>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <RangePicker value={range} onChange={v => { setRange(v); setOffset(0) }} />
          <Button size="sm" variant="secondary" onClick={reload}>
            <RefreshCw className="w-3.5 h-3.5" />
            <span>Refresh</span>
          </Button>
          <Button size="sm" variant="default" onClick={exportCsv}>
            <Download className="w-3.5 h-3.5 text-violet-400" />
            <span>Export CSV</span>
          </Button>
        </div>
      </div>

      {(error || exportError) && (
        <div className="text-xs text-rose-300 bg-rose-500/10 border border-rose-500/20 rounded-xl px-4 py-3">
          {error || exportError}
        </div>
      )}

      {/* Filter toolbar */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
        <div className="relative">
          <Search className="w-4 h-4 text-zinc-500 absolute left-3.5 top-1/2 -translate-y-1/2 pointer-events-none" />
          <Input
            placeholder="Search Trace ID, Model, or Error message..."
            value={search}
            onChange={e => { setSearch(e.target.value); setOffset(0) }}
            className="pl-9"
          />
        </div>
        <Select
          value={status}
          onChange={e => { setStatus(e.target.value); setOffset(0) }}
        >
          <option value="">All Status Codes</option>
          <option value="success">Success (2xx)</option>
          <option value="error">Errors</option>
          <option value="4xx">Client Errors (4xx)</option>
          <option value="5xx">Upstream Errors (5xx)</option>
        </Select>
        <Input
          placeholder="Filter by Intent (e.g. coding_agentic)"
          value={intent}
          onChange={e => { setIntent(e.target.value); setOffset(0) }}
        />
      </div>

      {/* Main Request Traces Table */}
      <Card>
        <CardBody className="p-0">
          {loading && !data ? (
            <Spinner />
          ) : !data?.traces?.length ? (
            <div className="p-12 text-center text-zinc-500 text-xs flex flex-col items-center gap-2">
              <Zap className="w-8 h-8 text-zinc-600 stroke-1" />
              <span>No request traces matched your query filters</span>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-white/[0.08] text-[10px] uppercase tracking-wider text-zinc-400 bg-white/[0.01]">
                    <th className="px-6 py-3.5 font-semibold">Timestamp</th>
                    <th className="px-6 py-3.5 font-semibold">Status</th>
                    <th className="px-6 py-3.5 font-semibold">Routed Model</th>
                    <th className="px-6 py-3.5 font-semibold">Intent</th>
                    <th className="px-6 py-3.5 font-semibold">Duration</th>
                    <th className="px-6 py-3.5 font-semibold">Tokens</th>
                    <th className="px-6 py-3.5 font-semibold">Est. Cost</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/[0.06]">
                  {data.traces.map(t => (
                    <tr
                      key={t.trace_id}
                      onClick={() => setSelected(t.trace_id)}
                      className={`hover:bg-white/[0.03] transition-colors cursor-pointer ${selected === t.trace_id ? 'bg-violet-500/15' : ''}`}
                    >
                      <td className="px-6 py-3.5 text-zinc-400 font-mono text-[11px] whitespace-nowrap">{fmtTime(t.created_at)}</td>
                      <td className="px-6 py-3.5">
                        <Badge variant={t.success ? 'ok' : 'err'}>
                          <StatusDot ok={!!t.success} />
                          {t.status_code ?? (t.success ? 200 : 'err')}
                        </Badge>
                      </td>
                      <td className="px-6 py-3.5 font-mono text-white font-semibold truncate max-w-[200px]">{t.model_routed || '—'}</td>
                      <td className="px-6 py-3.5 text-zinc-300 font-medium">{t.intent || '—'}</td>
                      <td className="px-6 py-3.5 font-mono text-zinc-200">{fmtMs(t.duration_ms)}</td>
                      <td className="px-6 py-3.5 font-mono text-zinc-300">{fmtTokens(t.total_tokens)}</td>
                      <td className="px-6 py-3.5 font-mono text-violet-300 font-semibold">{fmtUsd(t.estimated_cost_usd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardBody>
      </Card>

      {/* Pagination Footer */}
      <div className="flex justify-between items-center text-xs text-zinc-400 px-1">
        <span>{data ? `Showing ${Math.min(offset + 1, data.total)}–${Math.min(offset + 40, data.total)} of ${data.total} requests` : ''}</span>
        <div className="flex gap-2">
          <Button size="sm" variant="default" disabled={offset <= 0} onClick={() => setOffset(Math.max(0, offset - 40))}>
            <ChevronLeft className="w-4 h-4" />
            <span>Prev</span>
          </Button>
          <Button size="sm" variant="default" disabled={!data || offset + 40 >= data.total} onClick={() => setOffset(offset + 40)}>
            <span>Next</span>
            <ChevronRight className="w-4 h-4" />
          </Button>
        </div>
      </div>

      {/* Selected Trace Details Drawer / Modal Card */}
      {selected && (
        <Card className="border-violet-500/30">
          <CardHeader>
            <div className="flex items-center gap-3">
              <Activity className="w-4 h-4 text-violet-400" />
              <span className="text-xs font-mono font-bold text-white">Trace Detail: {selected}</span>
            </div>
            <div className="flex items-center gap-2">
              <CopyButton text={selected} />
              <button onClick={() => setSelected(null)} className="p-1 rounded-lg text-zinc-400 hover:text-white">
                <X className="w-4 h-4" />
              </button>
            </div>
          </CardHeader>
          <CardBody>
            {detailLoading ? (
              <Spinner />
            ) : detailError ? (
              <div className="text-xs text-rose-400">{detailError}</div>
            ) : !detail ? (
              <div className="text-xs text-zinc-500">Trace not found</div>
            ) : (
              <div className="space-y-6">
                <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 p-4 rounded-xl bg-zinc-950/80 border border-white/[0.06] text-xs">
                  <div>
                    <span className="text-zinc-500 text-[10px] uppercase font-semibold">Provider ID</span>
                    <p className="font-semibold text-white mt-1">{detail.provider_id || '—'}</p>
                  </div>
                  <div>
                    <span className="text-zinc-500 text-[10px] uppercase font-semibold">Intent Confidence</span>
                    <p className="font-semibold text-emerald-400 mt-1">{detail.intent_confidence ? `${(detail.intent_confidence * 100).toFixed(0)}%` : '—'}</p>
                  </div>
                  <div>
                    <span className="text-zinc-500 text-[10px] uppercase font-semibold">Estimated Cost</span>
                    <p className="font-semibold text-violet-300 mt-1">{fmtUsd(detail.estimated_cost_usd)}</p>
                  </div>
                  <div>
                    <span className="text-zinc-500 text-[10px] uppercase font-semibold">Upstream TTFT</span>
                    <p className="font-semibold text-white mt-1">{fmtMs(detail.upstream_ttft_ms)}</p>
                  </div>
                </div>

                {detail.chain && detail.chain.length > 0 && (
                  <div className="p-4 rounded-xl bg-zinc-950/60 border border-white/[0.06] text-xs">
                    <span className="text-zinc-400 font-medium">Fallback Evaluation Chain: </span>
                    <span className="font-mono text-violet-300 font-semibold">{detail.chain.join(' → ')}</span>
                    {detail.fallback_index ? <span className="text-amber-400 ml-2">(Succeeded at index {detail.fallback_index})</span> : null}
                  </div>
                )}

                {detail.error_message && (
                  <div className="p-4 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-300 text-xs font-mono">
                    {detail.error_message}
                  </div>
                )}

                <div>
                  <h4 className="text-[11px] font-semibold uppercase tracking-wider text-zinc-400 mb-3">Span Timeline Waterfall</h4>
                  <Waterfall spans={detail.spans || []} />
                </div>
              </div>
            )}
          </CardBody>
        </Card>
      )}
    </div>
  )
}
