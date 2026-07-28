import React, { useState, useEffect, useCallback } from 'react'
import { Card, CardBody, CardHeader, Badge, Button, StatusDot, StatBox, Skeleton, ErrorState, EmptyState } from '../components/ui'
import { useAnalyticsSSE, useAnalyticsSummary } from '../hooks/useAnalytics'
import { fmtMs, fmtTokens, fmtUsd, fmtTime, fmtPct } from '../lib/format'
import { api, okBody, errMsg } from '../lib/api'
import {
  Radio,
  Play,
  Pause,
  RefreshCw,
  FileText,
  AlertTriangle,
  Zap,
  Clock,
  Cpu
} from 'lucide-react'

export default function LiveFeedPage() {
  const { connected, events, paused, togglePause, authError, reconnect } = useAnalyticsSSE(true)
  const { data: summary } = useAnalyticsSummary('1h')
  const [logEnabled, setLogEnabled] = useState<boolean | null>(null)
  const [logPath, setLogPath] = useState<string | null>(null)
  const [logDir, setLogDir] = useState<string | null>(null)
  const [logBusy, setLogBusy] = useState(false)
  const [logMsg, setLogMsg] = useState<string | null>(null)

  const applyLogStatus = (r: Record<string, unknown>) => {
    setLogEnabled(Boolean(r.enabled))
    setLogPath(typeof r.file_path === 'string' ? r.file_path : null)
    setLogDir(typeof r.log_dir === 'string' ? r.log_dir : null)
  }

  const loadLogging = useCallback(async () => {
    const r = await api<Record<string, unknown>>('/admin/request-logging')
    if (r && okBody(r)) applyLogStatus(r)
  }, [])

  useEffect(() => { loadLogging() }, [loadLogging])

  async function toggleLogging() {
    if (logEnabled === null) return
    setLogBusy(true)
    setLogMsg(null)
    const r = await api<Record<string, unknown>>('/admin/request-logging', {
      method: 'PUT',
      body: JSON.stringify({ enabled: !logEnabled }),
    })
    setLogBusy(false)
    if (r && okBody(r)) {
      applyLogStatus(r)
      setLogMsg(r.enabled ? 'Request file logging enabled' : 'Request file logging disabled')
    } else {
      setLogMsg(errMsg(r, 'Failed to update logging'))
    }
  }

  return (
    <div className="space-y-6 animate-[fadeIn_0.25s_ease-out]">
      {/* Header controls */}
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2">
            <Radio className="w-5 h-5 text-violet-400" />
            <h2 className="text-lg font-bold text-white tracking-tight">Live Request Pipeline</h2>
          </div>
          <Badge variant={connected ? 'ok' : 'err'}>
            <StatusDot ok={connected} />
            {connected ? 'SSE CONNECTED' : 'DISCONNECTED'}
          </Badge>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {logEnabled !== null && (
            <Button size="sm" variant={logEnabled ? 'secondary' : 'default'} onClick={toggleLogging} disabled={logBusy}>
              <FileText className="w-3.5 h-3.5" />
              <span>{logBusy ? 'Updating…' : logEnabled ? 'File Logging: ON' : 'File Logging: OFF'}</span>
            </Button>
          )}
          {!connected && (
            <Button size="sm" variant="primary" onClick={reconnect}>
              <RefreshCw className="w-3.5 h-3.5" />
              <span>Reconnect</span>
            </Button>
          )}
          <Button size="sm" variant="default" onClick={togglePause}>
            {paused ? <Play className="w-3.5 h-3.5 text-emerald-400" /> : <Pause className="w-3.5 h-3.5 text-amber-400" />}
            <span>{paused ? 'Resume Feed' : 'Pause Stream'}</span>
          </Button>
        </div>
      </div>

      {logMsg && (
        <div className="text-xs text-zinc-300 bg-zinc-900 border border-white/[0.08] p-3 rounded-xl flex items-center gap-2 font-mono">
          <FileText className="w-4 h-4 text-violet-400 shrink-0" />
          <span>{logMsg}</span>
          {(logDir || logPath) && (
            <span className="text-zinc-500 ml-auto">[{logDir || logPath}]</span>
          )}
        </div>
      )}

      {authError && (
        <div className="text-xs text-amber-200 bg-amber-500/10 border border-amber-500/20 rounded-xl p-4 flex items-center gap-3">
          <AlertTriangle className="w-5 h-5 text-amber-400 shrink-0" />
          <div>
            <strong>Stream Connection Warning: </strong>
            Live SSE feed is attempting reconnection. Ensure your API session or API key is active.
          </div>
        </div>
      )}

      {/* Summary Banner */}
      {!summary ? (
        <Skeleton cards={4} />
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-4 gap-4">
          <StatBox label="Live RPM" value={(summary?.requests_per_minute ?? 0).toFixed(1)} icon={Zap} />
          <StatBox label="Error Rate" value={fmtPct(summary?.error_rate)} color="text-rose-400" icon={AlertTriangle} />
          <StatBox label="Avg TTFT" value={fmtMs(summary?.avg_ttft_ms)} icon={Clock} />
          <StatBox label="Active Models" value={summary?.unique_models ?? 0} icon={Cpu} />
        </div>
      )}

      {/* Live Event Stream */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Radio className="w-4 h-4 text-emerald-400 animate-pulse" />
            <h3 className="text-sm font-semibold text-white">Event Ingress Feed</h3>
          </div>
          <Badge variant="default" className="font-mono">
            {events.length} events buffered
          </Badge>
        </CardHeader>
        <CardBody className="p-0 max-h-[65vh] overflow-y-auto custom-scrollbar">
          {!events.length ? (
            <EmptyState title="Waiting for live requests" icon={Zap}>
              Send a chat completion request to stream live telemetry events.
              {logEnabled && (
                <div className="mt-2 text-[11px] text-zinc-400 font-mono">
                  File logging enabled — storing rotating log files (50 MB/file) under DB storage directory.
                </div>
              )}
            </EmptyState>
          ) : (
            events.map((e, i) => {
              const isReq = e.type === 'request'
              const ok = e.success !== false && !(e.status_code && e.status_code >= 400)
              const model = (e.model_routed || e.model_requested || '—')
              const ts = e.created_at || e.ts

              return (
                <div
                  key={(e.trace_id || e.id || '') + String(i)}
                  className={`p-4 border-b border-white/[0.06] hover:bg-white/[0.02] transition-colors text-xs space-y-2 animate-[fadeIn_0.2s_ease-out] ${
                    !ok ? 'bg-rose-500/5' : ''
                  }`}
                >
                  <div className="flex items-center gap-3 flex-wrap">
                    <span className="text-zinc-500 font-mono text-[11px] shrink-0">
                      {fmtTime(typeof ts === 'number' ? ts : undefined)}
                    </span>
                    <StatusDot ok={ok} />
                    {isReq && <Badge variant="accent">req</Badge>}
                    <span className="text-zinc-200 font-semibold">{e.intent || e.path || '—'}</span>
                    <span className="text-zinc-500">→</span>
                    <span className="font-mono text-violet-300 font-semibold">
                      {typeof model === 'string' ? model.split('/').pop() : '—'}
                    </span>
                    {(e.fallback_index ?? 0) > 0 && (
                      <Badge variant="warn">fallback[{e.fallback_index}]</Badge>
                    )}
                    {e.status_code != null && (
                      <Badge variant={ok ? 'ok' : 'err'}>
                        {e.status_code}
                      </Badge>
                    )}
                  </div>
                  <div className="ml-[4.5rem] text-[11px] text-zinc-400 flex gap-4 flex-wrap font-mono">
                    <span>Latency: {fmtMs(e.duration_ms)}</span>
                    {e.total_tokens != null && <span>Tokens: {fmtTokens(e.total_tokens)}</span>}
                    {e.estimated_cost_usd != null && <span>Cost: {fmtUsd(e.estimated_cost_usd)}</span>}
                    {e.error_message && <span className="text-rose-400 font-sans">{e.error_message}</span>}
                    {e.error && !e.error_message && <span className="text-rose-400 font-sans">{e.error}</span>}
                  </div>
                </div>
              )
            })
          )}
        </CardBody>
      </Card>
    </div>
  )
}
