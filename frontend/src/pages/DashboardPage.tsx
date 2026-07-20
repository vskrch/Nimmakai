import { useEffect } from 'react'
import { Card, CardBody, StatBox, Badge, StatusDot, Button, Spinner } from '../components/ui'
import { useHealth, useStats, useSSE } from '../hooks/useApi'

export default function DashboardPage({ onRefresh }: { onRefresh: () => void }) {
  const { data: health, reload: reloadHealth } = useHealth()
  const { data: stats } = useStats()
  const sse = useSSE()

  useEffect(() => {
    const id = setInterval(reloadHealth, 30000)
    return () => clearInterval(id)
  }, [reloadHealth])

  if (!health) return <Spinner />

  const providers = health.providers || []
  const runtimeP = providers.filter(p => p.runtime || (p.enabled && p.key_count > 0))
  const live = health.live_models ?? stats?.catalog?.live_model_count ?? 0
  const keys = health.keys_configured ?? 0
  const degraded = health.status === 'degraded'
  const statusText = (!runtimeP.length || keys === 0) ? 'Setup needed'
    : live === 0 ? 'No models' : degraded ? 'Degraded' : 'Operational'
  const statusColor = statusText === 'Operational' ? 'text-emerald-400'
    : statusText === 'Setup needed' || statusText === 'No models' ? 'text-red-400' : 'text-blue-400'

  return (
    <div className="animate-[fadeIn_0.3s_ease]">
      <div className="grid grid-cols-[repeat(auto-fit,minmax(200px,1fr))] gap-4 mb-8">
        <StatBox label="Status" value={statusText} sub={statusText === 'Operational' ? 'Routing active' : 'Check providers'} color={statusColor} />
        <StatBox label="Providers" value={providers.length} sub={`${runtimeP.length} with active keys`} />
        <StatBox label="Live Models" value={sse?.live_models ?? live} sub="across all APIs" />
        <StatBox label="Upstream Keys" value={keys} sub={`${sse?.active_providers ?? health.keys_available ?? 0} available`} />
        <StatBox label="Fallback Advances" value={sse?.fallback_advances ?? stats?.routing?.fallback_advances ?? 0} sub="route quality signal" />
      </div>

      {health.status !== 'ok' && (
        <Card>
          <CardBody>
            <h3 className="text-sm font-semibold mb-3">Production Setup Checklist</h3>
            <ol className="ml-5 flex flex-col gap-2 text-[13px] text-zinc-400 list-decimal">
              {!health.proxy_auth_configured && <li><strong className="text-white">PROXY_API_KEYS</strong> not set. Set in production or ALLOW_INSECURE_AUTH for local.</li>}
              {keys === 0 && <li><strong className="text-white">No upstream keys.</strong> Set NIM_API_KEYS or add a provider.</li>}
              {live === 0 && keys > 0 && <li><strong className="text-white">Catalog empty.</strong> Go to Models to refresh, or wait for background refresh.</li>}
            </ol>
          </CardBody>
        </Card>
      )}

      <Card>
        <CardBody>
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-sm font-semibold">Providers</h3>
            <Button size="sm" onClick={onRefresh}>Refresh All</Button>
          </div>
          <table className="w-full">
            <thead>
              <tr className="text-left">
                <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px] border-b border-white/[0.08]">Provider</th>
                <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px] border-b border-white/[0.08]">Keys</th>
                <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px] border-b border-white/[0.08]">Models</th>
                <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px] border-b border-white/[0.08]">Status</th>
              </tr>
            </thead>
            <tbody>
              {providers.map(p => {
                const active = p.runtime || (p.enabled && p.key_count > 0)
                return (
                  <tr key={p.id} className="border-b border-white/[0.08] last:border-0 hover:bg-white/[0.01]">
                    <td className="px-6 py-3.5 text-[13px]">
                      <strong>{p.id}</strong>
                    </td>
                    <td className="px-6 py-3.5 text-[13px] text-zinc-400">{p.key_count}</td>
                    <td className="px-6 py-3.5 text-[13px] text-violet-400 font-semibold">{sse?.provider_health?.[p.id] ? sse.provider_health[p.id].available_keys : '—'}</td>
                    <td className="px-6 py-3.5">
                      <Badge variant={active ? 'ok' : p.enabled ? 'err' : 'default'}>
                        <StatusDot ok={!!active} />
                        {!p.enabled ? 'Disabled' : active ? 'In pool' : 'No keys'}
                      </Badge>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </CardBody>
      </Card>
    </div>
  )
}
