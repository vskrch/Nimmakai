import { useState, useMemo } from 'react'
import { Card, CardBody, Badge, Button, Input, Spinner } from '../components/ui'
import { useCatalog, useProviders } from '../hooks/useApi'
import { ap, errMsg } from '../lib/api'

export default function ModelsPage() {
  const { data: catalog, reload: reloadCatalog } = useCatalog()
  const { data: providers } = useProviders()
  const [search, setSearch] = useState('')
  const [filterProv, setFilterProv] = useState<string | null>(null)
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null)

  if (!catalog) return <Spinner />

  const chains = catalog.dynamic_chains || {}
  const allModels = useMemo(() => {
    const seen = new Set<string>()
    const out: { id: string; provider: string; score?: number }[] = []
    for (const [, chain] of Object.entries(chains)) {
      for (const mid of chain) {
        if (seen.has(mid)) continue
        seen.add(mid)
        const parts = mid.split('/')
        out.push({ id: mid, provider: parts[0] || 'unknown' })
      }
    }
    return out.sort((a, b) => a.id.localeCompare(b.id))
  }, [chains])

  const providers_set = useMemo(() => {
    return [...new Set(allModels.map(m => m.provider))].sort()
  }, [allModels])

  const filtered = useMemo(() => {
    let ms = allModels
    if (filterProv) ms = ms.filter(m => m.provider === filterProv)
    if (search) {
      const q = search.toLowerCase()
      ms = ms.filter(m => m.id.toLowerCase().includes(q))
    }
    return ms
  }, [allModels, search, filterProv])

  async function handleRefresh() {
    const r = await ap('/admin/catalog/refresh', {})
    if (r && (r as Record<string, unknown>).ok !== false) {
      setMsg({ text: 'Catalog refreshed', ok: true })
      reloadCatalog()
    } else {
      setMsg({ text: errMsg(r, 'Refresh failed'), ok: false })
    }
  }

  async function handleQualityOverride(modelId: string, value: string) {
    const v = parseFloat(value)
    if (isNaN(v) || v < 0 || v > 100) { setMsg({ text: 'Quality must be 0-100', ok: false }); return }
    const parts = modelId.split('/')
    const r = await ap('/admin/models/register', {
      provider_id: parts[0],
      models: [parts.slice(1).join('/')],
      quality_override: v,
    })
    if (r && (r as Record<string, unknown>).ok) {
      setMsg({ text: `Quality override set for ${modelId}`, ok: true })
    } else {
      setMsg({ text: errMsg(r, 'Failed'), ok: false })
    }
  }

  return (
    <div className="animate-[fadeIn_0.3s_ease]">
      <div className="flex gap-3 mb-4 flex-wrap items-center">
        <Input placeholder="Search live models..." value={search} onChange={e => setSearch(e.target.value)} className="max-w-[300px]" />
        <Button onClick={handleRefresh}>Refresh Catalog</Button>
        <span className="text-xs text-zinc-400">{filtered.length} of {allModels.length} models</span>
      </div>

      {msg && (
        <div className={`mb-4 p-3 rounded-lg text-[13px] ${msg.ok ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-red-500/10 text-red-400 border border-red-500/20'}`}>
          {msg.text}
          <button className="ml-3 text-xs opacity-60" onClick={() => setMsg(null)}>dismiss</button>
        </div>
      )}

      <div className="flex flex-wrap gap-2 mb-4">
        <button
          onClick={() => setFilterProv(null)}
          className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all border ${!filterProv ? 'bg-violet-500/10 text-violet-300 border-violet-500/20' : 'bg-white/[0.03] text-zinc-400 border-white/[0.08] hover:bg-white/[0.05]'}`}
        >
          All ({allModels.length})
        </button>
        {providers_set.map(p => (
          <button
            key={p}
            onClick={() => setFilterProv(filterProv === p ? null : p)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all border ${filterProv === p ? 'bg-violet-500/10 text-violet-300 border-violet-500/20' : 'bg-white/[0.03] text-zinc-400 border-white/[0.08] hover:bg-white/[0.05]'}`}
          >
            {p} ({allModels.filter(m => m.provider === p).length})
          </button>
        ))}
      </div>

      <Card className="p-0">
        <table className="w-full">
          <thead>
            <tr className="text-left border-b border-white/[0.08]">
              <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px]">Model</th>
              <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px]">Provider</th>
              <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px] w-[120px]">Quality Override</th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr><td colSpan={3} className="px-6 py-8 text-center text-zinc-400">No models found. Add providers and refresh catalog.</td></tr>
            ) : (
              filtered.map(m => (
                <tr key={m.id} className="border-b border-white/[0.08] last:border-0 hover:bg-white/[0.01]">
                  <td className="px-6 py-3.5 text-[13px]">
                    <strong>{m.id.split('/').slice(1).join('/')}</strong>
                    <div className="text-[11px] text-zinc-500">{m.id}</div>
                  </td>
                  <td className="px-6 py-3.5"><Badge variant="accent">{m.provider}</Badge></td>
                  <td className="px-6 py-3.5">
                    <input
                      type="number"
                      min={0}
                      max={100}
                      placeholder="—"
                      className="bg-black/20 border border-white/[0.08] text-white px-2 py-1 rounded text-[11px] w-[60px] focus:outline-none focus:border-violet-500/50"
                      onBlur={e => { if (e.target.value) handleQualityOverride(m.id, e.target.value) }}
                    />
                    <span className="text-[11px] text-zinc-500 ml-1">0-100</span>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </Card>
    </div>
  )
}
