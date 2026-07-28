import React, { useEffect, useState } from 'react'
import { Card, CardBody, CardHeader, StatBox, Badge, StatusDot, Button, Skeleton, ErrorState } from '../components/ui'
import { useHealth, useStats, useSSE } from '../hooks/useApi'
import { api, okBody } from '../lib/api'
import { fmtMs, fmtTokens, fmtUsd, fmtPct, rangeSince, qs } from '../lib/format'
import type { AnalyticsSummary } from '../types/analytics'
import {
  Activity,
  Server,
  Cpu,
  Key,
  GitBranch,
  Clock,
  Coins,
  ShieldAlert,
  RefreshCw,
  CheckCircle2,
  AlertTriangle,
  Zap
} from 'lucide-react'

export default function DashboardPage({ onRefresh }: { onRefresh: () => void }) {
  const { data: health, reload: reloadHealth, loading: healthLoading, error: healthError } = useHealth()
  const { data: stats } = useStats()
  const sse = useSSE()
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null)

  useEffect(() => {
    const id = setInterval(reloadHealth, 30000)
    return () => clearInterval(id)
  }, [reloadHealth])

  useEffect(() => {
    ;(async () => {
      const r = await api<AnalyticsSummary>(`/analytics/summary${qs({ since: rangeSince('1h') })}`)
      if (okBody(r)) setSummary(r as AnalyticsSummary)
    })()
  }, [])

  if (healthLoading || !health) return (
    <div className="space-y-6 animate-[fadeIn_0.25s_ease-out]">
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="bg-zinc-900/60 backdrop-blur-xl border border-white/[0.08] rounded-2xl p-5 shadow-[0_4px_20px_rgba(0,0,0,0.2)]">
            <Skeleton lines={2} />
          </div>
        ))}
      </div>
      <Skeleton lines={6} />
    </div>
  )
  if (healthError) return <ErrorState title="Dashboard unavailable" message={healthError} onRetry={reloadHealth} />

  const providers = health.providers || []
  const runtimeP = providers.filter(p => p.runtime || (p.enabled && p.key_count > 0))
  const live = health.live_models ?? stats?.catalog?.live_model_count ?? 0
  const keys = health.keys_configured ?? 0
  const degraded = health.status === 'degraded'
  const statusText = (!runtimeP.length || keys === 0) ? 'Setup needed'
    : live === 0 ? 'No models' : degraded ? 'Degraded' : 'Operational'
  const statusColor = statusText === 'Operational' ? 'text-emerald-400'
    : statusText === 'Setup needed' || statusText === 'No models' ? 'text-rose-400' : 'text-amber-400'

  return (
    <div className="space-y-6 animate-[fadeIn_0.25s_ease-out]">
      {/* System Primary Metrics Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
        <StatBox
          label="Gateway Status"
          value={statusText}
          sub={statusText === 'Operational' ? 'Zero-downtime routing active' : 'Check keys & catalog'}
          color={statusColor}
          icon={Activity}
        />
        <StatBox
          label="Active Providers"
          value={providers.length}
          sub={`${runtimeP.length} configured with active keys`}
          icon={Server}
        />
        <StatBox
          label="Live Model Pool"
          value={sse?.live_models ?? live}
          sub="Across all connected APIs"
          icon={Cpu}
        />
        <StatBox
          label="Upstream Keys"
          value={keys}
          sub={`${sse?.active_providers ?? health.keys_available ?? 0} active runtimes`}
          icon={Key}
        />
        <StatBox
          label="Fallback Advances"
          value={(sse?.fallback_advances ?? stats?.routing?.fallback_advances ?? 0).toLocaleString()}
          sub="Self-healing route switches"
          icon={GitBranch}
        />
      </div>

      {/* 1-Hour Telemetry Summary */}
      {summary && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          <StatBox
            label="Total Requests (1h)"
            value={summary.total_requests.toLocaleString()}
            sub={`${summary.requests_per_minute.toFixed(1)} req/min average`}
            icon={Zap}
          />
          <StatBox
            label="Avg / P95 Latency"
            value={fmtMs(summary.avg_latency_ms)}
            sub={`p95 ${fmtMs(summary.p95_latency_ms)}`}
            icon={Clock}
          />
          <StatBox
            label="Total Tokens (1h)"
            value={fmtTokens(summary.total_tokens)}
            sub={`Success rate: ${fmtPct(summary.success_rate)}`}
            icon={Cpu}
          />
          <StatBox
            label="Est. Expenditure"
            value={fmtUsd(summary.estimated_cost_usd)}
            sub={`Error rate: ${fmtPct(summary.error_rate)}`}
            icon={Coins}
          />
        </div>
      )}

      {/* Production Setup Checklist Warning */}
      {health.status !== 'ok' && (
        <Card className="border-amber-500/30 bg-amber-500/5">
          <CardBody>
            <div className="flex items-center gap-3 mb-3">
              <ShieldAlert className="w-5 h-5 text-amber-400" />
              <h3 className="text-sm font-semibold text-amber-200">Production Setup Checklist</h3>
            </div>
            <ol className="ml-6 space-y-2 text-xs text-amber-300/90 list-decimal font-medium">
              {!health.proxy_auth_configured && (
                <li>
                  <strong className="text-white">PROXY_API_KEYS</strong> environment variable not configured. Set security keys for production, or set <code className="bg-black/40 px-1 py-0.5 rounded text-amber-200">ALLOW_INSECURE_AUTH=true</code> for local sandbox testing.
                </li>
              )}
              {keys === 0 && (
                <li>
                  <strong className="text-white">No upstream provider keys.</strong> Add API keys via the Providers tab or configure environment keys (e.g. <code className="bg-black/40 px-1 py-0.5 rounded text-amber-200">OPENCODE_ZEN_API_KEYS</code>).
                </li>
              )}
              {live === 0 && keys > 0 && (
                <li>
                  <strong className="text-white">Model catalog is empty.</strong> Click "Refresh Catalog" above or navigate to the Models tab to trigger initial API probing.
                </li>
              )}
            </ol>
          </CardBody>
        </Card>
      )}

      {/* Connected Providers Overview Table */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <Server className="w-4 h-4 text-violet-400" />
            <h3 className="text-sm font-semibold text-white">Provider Connectivity & Runtimes</h3>
          </div>
          <Button size="sm" variant="secondary" onClick={onRefresh}>
            <RefreshCw className="w-3.5 h-3.5" />
            <span>Refresh All</span>
          </Button>
        </CardHeader>
        <CardBody className="p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs min-w-[560px]">
              <thead>
                <tr className="border-b border-white/[0.08] text-[10px] uppercase tracking-wider text-zinc-400 bg-white/[0.01]">
                  <th className="px-4 sm:px-6 py-3.5 font-semibold">Provider ID</th>
                  <th className="px-4 sm:px-6 py-3.5 font-semibold">API Keys</th>
                  <th className="px-4 sm:px-6 py-3.5 font-semibold">Available Capacity</th>
                  <th className="px-4 sm:px-6 py-3.5 font-semibold">Runtime Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.06]">
                {providers.map(p => {
                  const active = p.runtime || (p.enabled && p.key_count > 0)
                  return (
                    <tr key={p.id} className="hover:bg-white/[0.02] transition-colors">
                      <td className="px-4 sm:px-6 py-4 font-semibold text-white flex items-center gap-2">
                        <span className="w-2 h-2 rounded-full bg-violet-400 shrink-0" />
                        <span>{p.id}</span>
                      </td>
                      <td className="px-4 sm:px-6 py-4 text-zinc-300 font-mono">{p.key_count} keys</td>
                      <td className="px-4 sm:px-6 py-4 text-violet-300 font-mono font-semibold">
                        {sse?.provider_health?.[p.id] ? `${sse.provider_health[p.id].available_keys} ready` : 'Active'}
                      </td>
                      <td className="px-4 sm:px-6 py-4">
                        <Badge variant={active ? 'ok' : p.enabled ? 'warn' : 'default'}>
                          <StatusDot ok={!!active} />
                          {!p.enabled ? 'Disabled' : active ? 'Active in Pool' : 'Missing Keys'}
                        </Badge>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </CardBody>
      </Card>
    </div>
  )
}
