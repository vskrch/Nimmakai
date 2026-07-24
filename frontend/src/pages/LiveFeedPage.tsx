import { useState, useEffect, useCallback } from 'react'
import { Card, CardBody, CardHeader, Badge, Button, StatusDot, StatBox } from '../components/ui'
import { useAnalyticsSSE, useAnalyticsSummary } from '../hooks/useAnalytics'
import { fmtMs, fmtTokens, fmtUsd, fmtTime, fmtPct } from '../lib/format'
import { api, okBody, errMsg } from '../lib/api'

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
    <div className="animate-[fadeIn_0.3s_ease]">
      <div className="flex items-center justify-between mb-6 gap-3 flex-wrap">
        <div className="flex items-center gap-3">
          <h2 className="text-xl font-semibold">Live Feed</h2>
          <Badge variant={connected ? 'ok' : 'err'}>
            <StatusDot ok={connected} />
            {connected ? 'LIVE' : 'Disconnected'}
          </Badge>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {logEnabled !== null && (
            <Button size="sm" onClick={toggleLogging} disabled={logBusy}>
              {logBusy ? '…' : logEnabled ? 'File logging: ON' : 'File logging: OFF'}
            </Button>
          )}
          {!connected && (
            <Button size="sm" onClick={reconnect}>Reconnect</Button>
          )}
          <Button size="sm" onClick={togglePause}>{paused ? 'Resume' : 'Pause'}</Button>
        </div>
      </div>

      {logMsg && (
        <div className="mb-3 text-xs text-zinc-400">
          {logMsg}
          {(logDir || logPath) && (
            <span className="ml-2 font-mono text-zinc-500">{logDir || logPath}</span>
          )}
        </div>
      )}

      {authError && (
        <div className="mb-4 text-sm text-amber-300 bg-amber-500/10 border border-amber-500/20 rounded-lg px-3 py-2">
          Live feed is having trouble staying connected. Try Reconnect, or confirm you are
          signed in / have an API key set. Events still appear once the stream recovers.
        </div>
      )}

      <div className="grid grid-cols-[repeat(auto-fit,minmax(140px,1fr))] gap-3 mb-6">
        <StatBox label="RPM" value={(summary?.requests_per_minute ?? 0).toFixed(1)} />
        <StatBox label="Error" value={fmtPct(summary?.error_rate)} color="text-red-400" />
        <StatBox label="TTFT" value={fmtMs(summary?.avg_ttft_ms)} />
        <StatBox label="Active models" value={summary?.unique_models ?? 0} />
      </div>

      <Card>
        <CardHeader>
          <h3 className="text-sm font-semibold">Request stream</h3>
          <span className="text-xs text-zinc-500">{events.length} buffered</span>
        </CardHeader>
        <CardBody className="p-0 max-h-[60vh] overflow-y-auto">
          {!events.length && (
            <div className="p-8 text-center text-zinc-500 text-sm">
              Waiting for requests… Send a chat completion to see live events.
              {logEnabled && (
                <div className="mt-2 text-xs text-zinc-600">
                  File logging is on — rotating dated logs (50 MB/file, 90‑day retention) beside the DB.
                </div>
              )}
            </div>
          )}
          {events.map((e, i) => {
            const isReq = e.type === 'request'
            const ok = e.success !== false && !(e.status_code && e.status_code >= 400)
            const model = (e.model_routed || e.model_requested || '—')
            const ts = e.created_at || e.ts
            return (
              <div
                key={(e.trace_id || e.id || '') + String(i)}
                className={`px-5 py-3 border-b border-white/[0.06] animate-[fadeIn_0.25s_ease] ${
                  !ok ? 'bg-red-500/[0.06]' : ''
                }`}
              >
                <div className="flex items-center gap-3 text-[13px]">
                  <span className="text-zinc-500 font-mono text-[11px] w-16 shrink-0">
                    {fmtTime(typeof ts === 'number' ? ts : undefined)}
                  </span>
                  <StatusDot ok={ok} />
                  {isReq && <Badge variant="accent">req</Badge>}
                  <span className="text-zinc-300">{e.intent || e.path || '—'}</span>
                  <span className="text-zinc-500">→</span>
                  <span className="font-medium">
                    {typeof model === 'string' ? model.split('/').pop() : '—'}
                  </span>
                  {(e.fallback_index ?? 0) > 0 && (
                    <Badge variant="accent">fallback[{e.fallback_index}]</Badge>
                  )}
                  {e.status_code != null && (
                    <span className="text-zinc-500 tabular-nums">{e.status_code}</span>
                  )}
                </div>
                <div className="mt-1 ml-[4.5rem] text-[12px] text-zinc-500 flex gap-4 flex-wrap">
                  <span>{fmtMs(e.duration_ms)}</span>
                  {e.total_tokens != null && <span>{fmtTokens(e.total_tokens)} tokens</span>}
                  {e.estimated_cost_usd != null && <span>{fmtUsd(e.estimated_cost_usd)}</span>}
                  {e.error_message && <span className="text-red-400">{e.error_message}</span>}
                  {e.error && !e.error_message && <span className="text-red-400">{e.error}</span>}
                </div>
              </div>
            )
          })}
        </CardBody>
      </Card>
    </div>
  )
}
