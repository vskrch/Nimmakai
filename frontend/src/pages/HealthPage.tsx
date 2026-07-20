import { Card, CardHeader, CardBody, Badge, Button, Spinner, StatusDot } from '../components/ui'
import { useProviderHealth } from '../hooks/useApi'

export default function HealthPage() {
  const { data, reload } = useProviderHealth()

  if (!data) return <Spinner />

  const providers = data.providers || {}
  const keys = Object.keys(providers).sort()

  return (
    <div className="animate-[fadeIn_0.3s_ease]">
      <div className="flex items-center gap-3 mb-6">
        <h2 className="text-xl font-semibold">Provider Health</h2>
        <Button size="sm" onClick={reload}>Refresh</Button>
      </div>

      {keys.length === 0 && (
        <Card>
          <CardBody className="text-center text-zinc-400">No providers configured.</CardBody>
        </Card>
      )}

      {keys.map(pid => {
        const p = providers[pid]
        const hScore = p.aggregate_health ?? 1
        const barColor = hScore > 0.8 ? 'bg-emerald-500' : hScore > 0.5 ? 'bg-blue-500' : 'bg-red-500'
        const textColor = hScore > 0.8 ? 'text-emerald-400' : hScore > 0.5 ? 'text-blue-400' : 'text-red-400'
        const cbColor = p.circuit_breaker === 'open' ? 'text-red-400' : p.circuit_breaker === 'half_open' ? 'text-blue-400' : 'text-emerald-400'
        const models = p.models || {}
        const modelKeys = Object.keys(models).sort()

        return (
          <Card key={pid}>
            <CardHeader>
              <div className="flex items-center gap-3 flex-1">
                <strong className="text-[15px]">{pid}</strong>
                <Badge variant={p.enabled && p.runtime ? 'ok' : 'err'}>
                  {p.enabled && p.runtime ? 'Active' : 'Inactive'}
                </Badge>
                <span className={`text-[11px] font-semibold ${cbColor}`}>CB: {p.circuit_breaker}</span>
              </div>
              <div className="flex items-center gap-3">
                <span className="text-xs text-zinc-400">{p.model_count} models · {p.available_keys} keys</span>
                <div className="w-20 h-1.5 bg-[#050505] rounded overflow-hidden">
                  <div className={`h-full rounded ${barColor}`} style={{ width: `${(hScore * 100)}%` }} />
                </div>
                <span className={`text-xs font-semibold ${textColor}`}>{(hScore * 100).toFixed(0)}%</span>
              </div>
            </CardHeader>
            {modelKeys.length > 0 && (
              <CardBody className="p-0">
                <table className="w-full">
                  <thead>
                    <tr className="text-left border-b border-white/[0.08]">
                      <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px]">Model</th>
                      <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px]">Status</th>
                      <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px]">TPS</th>
                      <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px]">Latency</th>
                      <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px]">Error Rate</th>
                    </tr>
                  </thead>
                  <tbody>
                    {modelKeys.map(mid => {
                      const m = models[mid]
                      const ok = m.ok !== false
                      return (
                        <tr key={mid} className="border-b border-white/[0.08] last:border-0 hover:bg-white/[0.01]">
                          <td className="px-6 py-3 text-[12px]">{mid.split('/').slice(1).join('/')}</td>
                          <td className="px-6 py-3">
                            <Badge variant={ok ? 'ok' : 'err'}>{ok ? 'Healthy' : m.cooldown ? 'Cooldown' : 'Unhealthy'}</Badge>
                          </td>
                          <td className="px-6 py-3 text-[12px] text-zinc-400">{m.ewma_tok_per_s || '—'}</td>
                          <td className="px-6 py-3 text-[12px] text-zinc-400">{m.ewma_latency_s != null ? `${m.ewma_latency_s}s` : '—'}</td>
                          <td className={`px-6 py-3 text-[12px] ${m.error_rate > 0.3 ? 'text-red-400' : 'text-zinc-400'}`}>
                            {m.error_rate != null ? `${(m.error_rate * 100).toFixed(1)}%` : '—'}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </CardBody>
            )}
          </Card>
        )
      })}
    </div>
  )
}
