import { Card, CardBody, CardHeader, Badge, Button, StatusDot, StatBox } from '../components/ui'
import { useAnalyticsSSE, useAnalyticsSummary } from '../hooks/useAnalytics'
import { fmtMs, fmtTokens, fmtUsd, fmtTime, fmtPct } from '../lib/format'

export default function LiveFeedPage() {
  const { connected, events, paused, togglePause } = useAnalyticsSSE(true)
  const { data: summary } = useAnalyticsSummary('1h')

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
        <Button size="sm" onClick={togglePause}>{paused ? 'Resume' : 'Pause'}</Button>
      </div>

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
              Waiting for traces… Send a chat completion to see live events.
            </div>
          )}
          {events.map((e, i) => (
            <div
              key={(e.trace_id || '') + i}
              className={`px-5 py-3 border-b border-white/[0.06] animate-[fadeIn_0.25s_ease] ${
                e.success === false ? 'bg-red-500/[0.06]' : ''
              }`}
            >
              <div className="flex items-center gap-3 text-[13px]">
                <span className="text-zinc-500 font-mono text-[11px] w-16 shrink-0">{fmtTime(e.created_at)}</span>
                <StatusDot ok={e.success !== false} />
                <span className="text-zinc-300">{e.intent || '—'}</span>
                <span className="text-zinc-500">→</span>
                <span className="font-medium">{(e.model_routed || 'FAILED').split('/').pop()}</span>
                {(e.fallback_index ?? 0) > 0 && <Badge variant="accent">fallback[{e.fallback_index}]</Badge>}
              </div>
              <div className="mt-1 ml-[4.5rem] text-[12px] text-zinc-500 flex gap-4 flex-wrap">
                <span>{fmtMs(e.duration_ms)}</span>
                <span>{fmtTokens(e.total_tokens)} tokens</span>
                <span>{fmtUsd(e.estimated_cost_usd)}</span>
                {e.error_message && <span className="text-red-400">{e.error_message}</span>}
              </div>
            </div>
          ))}
        </CardBody>
      </Card>
    </div>
  )
}
