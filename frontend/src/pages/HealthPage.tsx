import React from 'react'
import { Card, CardHeader, CardBody, Badge, Button, Spinner, StatusDot, Skeleton } from '../components/ui'
import { useProviderHealth } from '../hooks/useApi'
import {
  Activity,
  RefreshCw,
  ShieldCheck,
  AlertTriangle,
  Cpu,
  Zap,
  Clock,
  Server
} from 'lucide-react'

export default function HealthPage() {
  const { data, reload, loading, error } = useProviderHealth()

  if (loading) return (
    <div className="space-y-6 animate-[fadeIn_0.25s_ease-out]">
      <Skeleton lines={3} />
      <Card><CardBody><Skeleton lines={4} /></CardBody></Card>
    </div>
  )
  if (error) return (
    <div className="space-y-6 animate-[fadeIn_0.25s_ease-out]">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <Activity className="w-5 h-5 text-violet-400" />
          <h2 className="text-lg font-bold text-white tracking-tight">Provider Health & Circuit Breakers</h2>
        </div>
        <Button size="sm" variant="secondary" onClick={reload}>
          <RefreshCw className="w-3.5 h-3.5" />
          <span>Refresh Telemetry</span>
        </Button>
      </div>
      <div className="p-4 rounded-xl text-xs text-rose-300 bg-rose-500/10 border border-rose-500/20">
        {error}
      </div>
    </div>
  )
  if (!data) return <Spinner />

  const providers = data.providers || {}
  const keys = Object.keys(providers).sort()

  return (
    <div className="space-y-6 animate-[fadeIn_0.25s_ease-out]">
      {/* Header controls */}
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-2 min-w-0">
          <Activity className="w-5 h-5 text-violet-400 shrink-0" />
          <h2 className="text-base sm:text-lg font-bold text-white tracking-tight truncate">Provider Health & Circuit Breakers</h2>
        </div>
        <Button size="sm" variant="secondary" onClick={reload}>
          <RefreshCw className="w-3.5 h-3.5" />
          <span>Refresh Telemetry</span>
        </Button>
      </div>

      {keys.length === 0 && (
        <Card>
          <CardBody className="p-12 text-center text-zinc-500 text-xs flex flex-col items-center gap-2">
            <Server className="w-8 h-8 text-zinc-600 stroke-1" />
            <span>No providers configured in the routing hub</span>
          </CardBody>
        </Card>
      )}

      {keys.map(pid => {
        const p = providers[pid]
        const hScore = p.aggregate_health ?? 1
        const barColor = hScore > 0.8 ? 'bg-emerald-500' : hScore > 0.5 ? 'bg-amber-500' : 'bg-rose-500'
        const textColor = hScore > 0.8 ? 'text-emerald-400' : hScore > 0.5 ? 'text-amber-400' : 'text-rose-400'
        const cbColor = p.circuit_breaker === 'open' ? 'text-rose-400' : p.circuit_breaker === 'half_open' ? 'text-amber-400' : 'text-emerald-400'
        const CbIcon = p.circuit_breaker === 'open' ? AlertTriangle : ShieldCheck
        const models = p.models || {}
        const modelKeys = Object.keys(models).sort()

        return (
          <Card key={pid}>
            <CardHeader>
              <div className="flex items-center gap-3 flex-1 flex-wrap">
                <strong className="text-sm font-bold text-white font-mono">{pid}</strong>
                <Badge variant={p.enabled && p.runtime ? 'ok' : 'err'}>
                  <StatusDot ok={!!(p.enabled && p.runtime)} />
                  {p.enabled && p.runtime ? 'Active In Pool' : 'Inactive'}
                </Badge>
                <span className={`text-xs font-semibold flex items-center gap-1 ${cbColor}`}>
                  <CbIcon className="w-3.5 h-3.5" />
                  CB: {p.circuit_breaker}
                </span>
              </div>
              <div className="flex items-center gap-3 sm:gap-4 w-full sm:w-auto justify-between sm:justify-start">
                <span className="text-xs text-zinc-400 font-mono">{p.model_count} models · {p.available_keys} keys</span>
                <div className="flex items-center gap-2">
                  <div className="w-16 sm:w-24 h-2 bg-zinc-950 rounded-full overflow-hidden border border-white/[0.08]">
                    <div className={`h-full rounded-full ${barColor} transition-all duration-500`} style={{ width: `${(hScore * 100)}%` }} />
                  </div>
                  <span className={`text-xs font-bold font-mono ${textColor}`}>{(hScore * 100).toFixed(0)}%</span>
                </div>
              </div>
            </CardHeader>

            {modelKeys.length > 0 && (
              <CardBody className="p-0">
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs min-w-[640px]">
                    <thead>
                      <tr className="border-b border-white/[0.08] text-[10px] uppercase tracking-wider text-zinc-400 bg-white/[0.01]">
                        <th className="px-4 sm:px-6 py-3.5 font-semibold">Model Slug</th>
                        <th className="px-4 sm:px-6 py-3.5 font-semibold">Health Status</th>
                        <th className="px-4 sm:px-6 py-3.5 font-semibold">Measured TPS</th>
                        <th className="px-4 sm:px-6 py-3.5 font-semibold">EWMA Latency</th>
                        <th className="px-4 sm:px-6 py-3.5 font-semibold">Error Rate</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-white/[0.06]">
                      {modelKeys.map(mid => {
                        const m = models[mid]
                        const ok = m.ok !== false
                        return (
                          <tr key={mid} className="hover:bg-white/[0.02] transition-colors">
                            <td className="px-4 sm:px-6 py-3.5 font-mono text-zinc-200 font-semibold">{mid.split('/').slice(1).join('/')}</td>
                            <td className="px-4 sm:px-6 py-3.5">
                              <Badge variant={ok ? 'ok' : 'err'}>
                                <StatusDot ok={ok} />
                                {ok ? 'Healthy' : m.cooldown ? 'Cooldown Active' : 'Degraded'}
                              </Badge>
                            </td>
                            <td className="px-4 sm:px-6 py-3.5 font-mono text-violet-300 font-semibold">{m.ewma_tok_per_s || '—'} tok/s</td>
                            <td className="px-4 sm:px-6 py-3.5 font-mono text-zinc-300">{m.ewma_latency_ms != null ? `${m.ewma_latency_ms}ms` : '—'}</td>
                            <td className={`px-4 sm:px-6 py-3.5 font-mono font-semibold ${m.error_rate > 0.3 ? 'text-rose-400' : 'text-emerald-400'}`}>
                              {m.error_rate != null ? `${(m.error_rate * 100).toFixed(1)}%` : '0%'}
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>
                </div>
              </CardBody>
            )}
          </Card>
        )
      })}
    </div>
  )
}
