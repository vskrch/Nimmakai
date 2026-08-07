import React, { useState, useMemo } from 'react'
import { Card, CardBody, Badge, Button, Input, Spinner, Switch } from '../components/ui'
import { useCatalog } from '../hooks/useApi'
import { ap, errMsg } from '../lib/api'
import {
  Cpu,
  Search,
  RefreshCw,
  Sliders,
  CheckCircle2,
  XCircle,
  Filter,
  Layers
} from 'lucide-react'

export default function ModelsPage() {
  const { data: catalog, reload: reloadCatalog } = useCatalog()
  const [search, setSearch] = useState('')
  const [filterProv, setFilterProv] = useState<string | null>(null)
  const [showDisabledOnly, setShowDisabledOnly] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null)

  const liveIds = catalog?.live_ids || []
  const disabledSet = useMemo(
    () => new Set(catalog?.disabled_models || []),
    [catalog?.disabled_models],
  )

  const allModels = useMemo(() => {
    const fromLive = liveIds.map(id => {
      const parts = id.split('/')
      return {
        id,
        provider: parts[0] || 'unknown',
        name: parts.slice(1).join('/') || id,
        enabled: !disabledSet.has(id),
      }
    })
    if (fromLive.length === 0 && catalog?.dynamic_chains) {
      const seen = new Set<string>()
      const out: { id: string; provider: string; name: string; enabled: boolean }[] = []
      for (const chain of Object.values(catalog.dynamic_chains)) {
        for (const mid of chain) {
          if (seen.has(mid)) continue
          seen.add(mid)
          const parts = mid.split('/')
          out.push({
            id: mid,
            provider: parts[0] || 'unknown',
            name: parts.slice(1).join('/') || mid,
            enabled: !disabledSet.has(mid),
          })
        }
      }
      return out.sort((a, b) => a.id.localeCompare(b.id))
    }
    return fromLive.sort((a, b) => a.id.localeCompare(b.id))
  }, [liveIds, disabledSet, catalog?.dynamic_chains])

  const providers = useMemo(
    () => [...new Set(allModels.map(m => m.provider))].sort(),
    [allModels],
  )

  const filtered = useMemo(() => {
    let ms = allModels
    if (filterProv) ms = ms.filter(m => m.provider === filterProv)
    if (showDisabledOnly) ms = ms.filter(m => !m.enabled)
    if (search) {
      const q = search.toLowerCase()
      ms = ms.filter(m => m.id.toLowerCase().includes(q))
    }
    return ms
  }, [allModels, search, filterProv, showDisabledOnly])

  const activeCount = allModels.filter(m => m.enabled).length
  const disabledCount = allModels.length - activeCount

  async function handleRefresh() {
    setBusy('refresh')
    const r = await ap('/admin/catalog/refresh', {})
    setBusy(null)
    if (r && (r as Record<string, unknown>).ok) {
      setMsg({ text: 'Catalog refreshed successfully', ok: true })
      reloadCatalog()
    } else {
      setMsg({ text: errMsg(r, 'Refresh failed'), ok: false })
    }
  }

  async function toggleModel(modelId: string, enabled: boolean) {
    setBusy(modelId)
    const r = await ap('/admin/models/set-enabled', { model_id: modelId, enabled })
    setBusy(null)
    if (r && (r as Record<string, unknown>).ok) {
      setMsg({
        text: enabled ? `Enabled ${modelId}` : `Disabled ${modelId} (removed from active pool)`,
        ok: true,
      })
      reloadCatalog()
    } else {
      setMsg({ text: errMsg(r, 'Failed to update model status'), ok: false })
    }
  }

  async function bulkProvider(providerId: string, enableAll: boolean) {
    setBusy(`bulk:${providerId}`)
    const r = await ap('/admin/models/bulk-enabled', {
      provider_id: providerId,
      enable_all: enableAll || undefined,
      disable_all: enableAll ? undefined : true,
    })
    setBusy(null)
    if (r && (r as Record<string, unknown>).ok) {
      setMsg({
        text: enableAll ? `Enabled all ${providerId} models` : `Disabled all ${providerId} models`,
        ok: true,
      })
      reloadCatalog()
    } else {
      setMsg({ text: errMsg(r, 'Bulk update failed'), ok: false })
    }
  }

  async function handleQualityOverride(modelId: string, value: string) {
    const v = parseFloat(value)
    if (isNaN(v) || v < 0 || v > 100) {
      setMsg({ text: 'Quality rating must be between 0 and 100', ok: false })
      return
    }
    const parts = modelId.split('/')
    const r = await ap('/admin/models/register', {
      provider_id: parts[0],
      models: [parts.slice(1).join('/')],
      quality_override: v,
    })
    if (r && (r as Record<string, unknown>).ok) {
      setMsg({ text: `Quality score override updated for ${modelId}`, ok: true })
    } else {
      setMsg({ text: errMsg(r, 'Failed to save quality override'), ok: false })
    }
  }

  if (!catalog) return <Spinner />

  return (
    <div className="space-y-6 animate-[fadeIn_0.25s_ease-out]">
      {/* Header controls */}
      <div className="flex justify-between items-start gap-4 flex-wrap">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <Cpu className="w-5 h-5 text-violet-400 shrink-0" />
            <h2 className="text-base sm:text-lg font-bold text-white tracking-tight truncate">Model Pool & Capability Catalog</h2>
          </div>
          <p className="text-zinc-400 text-xs mt-1 max-w-[620px]">
            Manage active models across all connected providers. Disabled models remain discovered in system metadata but are excluded from active fallback chains.
          </p>
        </div>
        <Button variant="secondary" onClick={handleRefresh} disabled={busy === 'refresh'} className="shrink-0">
          <RefreshCw className={`w-3.5 h-3.5 ${busy === 'refresh' ? 'animate-spin' : ''}`} />
          <span className="hidden sm:inline">{busy === 'refresh' ? 'Probing Endpoints…' : 'Refresh Catalog'}</span>
          <span className="sm:hidden">Refresh</span>
        </Button>
      </div>

      {msg && (
        <div className={`p-3 sm:p-4 rounded-xl text-xs flex items-start sm:items-center justify-between font-medium gap-3 ${
          msg.ok ? 'bg-emerald-500/10 text-emerald-300 border border-emerald-500/20' : 'bg-rose-500/10 text-rose-300 border border-rose-500/20'
        }`}>
          <span className="break-words">{msg.text}</span>
          <button className="text-zinc-400 hover:text-white shrink-0" onClick={() => setMsg(null)}>Dismiss</button>
        </div>
      )}

      {/* Filter toolbar */}
      <div className="flex flex-col sm:flex-row gap-3 flex-wrap items-start sm:items-center justify-between">
        <div className="flex flex-col sm:flex-row items-start sm:items-center gap-3 flex-1 w-full sm:w-auto">
          <div className="relative w-full sm:min-w-[220px] sm:max-w-[320px]">
            <Search className="w-4 h-4 text-zinc-500 absolute left-3.5 top-1/2 -translate-y-1/2 pointer-events-none" />
            <Input
              placeholder="Search model name or ID..."
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="pl-9"
            />
          </div>
          <button
            type="button"
            onClick={() => setShowDisabledOnly(v => !v)}
            className={`px-3 py-2 rounded-xl text-xs font-semibold border transition-all cursor-pointer whitespace-nowrap ${
              showDisabledOnly
                ? 'bg-amber-500/20 text-amber-300 border-amber-500/30'
                : 'bg-zinc-900 text-zinc-400 border-white/[0.08] hover:text-white'
            }`}
          >
            Disabled Only
          </button>
        </div>
        <div className="text-xs text-zinc-400 font-mono shrink-0">
          <span>{filtered.length} visible · </span>
          <strong className="text-emerald-400">{activeCount} active</strong> ·{' '}
          <strong className="text-rose-400">{disabledCount} disabled</strong>
        </div>
      </div>

      {/* Provider Filter Chips */}
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => setFilterProv(null)}
          className={`px-3 py-1.5 rounded-xl text-xs font-semibold transition-all border cursor-pointer ${
            !filterProv
              ? 'bg-violet-500/20 text-violet-200 border-violet-500/30'
              : 'bg-zinc-900 text-zinc-400 border-white/[0.08] hover:bg-white/[0.04]'
          }`}
        >
          All Providers ({allModels.length})
        </button>
        {providers.map(p => {
          const n = allModels.filter(m => m.provider === p).length
          const on = allModels.filter(m => m.provider === p && m.enabled).length
          return (
            <button
              key={p}
              type="button"
              onClick={() => setFilterProv(filterProv === p ? null : p)}
              className={`px-3 py-1.5 rounded-xl text-xs font-semibold transition-all border cursor-pointer ${
                filterProv === p
                  ? 'bg-violet-500/20 text-violet-200 border-violet-500/30'
                  : 'bg-zinc-900 text-zinc-400 border-white/[0.08] hover:bg-white/[0.04]'
              }`}
            >
              {p} ({on}/{n})
            </button>
          )
        })}
      </div>

      {filterProv && (
        <div className="flex flex-col sm:flex-row gap-2 p-3 rounded-xl bg-zinc-950/60 border border-white/[0.06] items-start sm:items-center justify-between">
          <span className="text-xs text-zinc-300 font-medium">Bulk operations for provider: <strong className="text-violet-400">{filterProv}</strong></span>
          <div className="flex gap-2 w-full sm:w-auto">
            <Button size="xs" variant="secondary" onClick={() => bulkProvider(filterProv, true)} disabled={busy === `bulk:${filterProv}`} className="flex-1 sm:flex-none">
              Enable All {filterProv}
            </Button>
            <Button size="xs" variant="danger" onClick={() => bulkProvider(filterProv, false)} disabled={busy === `bulk:${filterProv}`} className="flex-1 sm:flex-none">
              Disable All {filterProv}
            </Button>
          </div>
        </div>
      )}

      {/* Model Table */}
      <Card>
        <CardBody className="p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs min-w-[720px]">
              <thead>
                <tr className="border-b border-white/[0.08] text-[10px] uppercase tracking-wider text-zinc-400 bg-white/[0.01]">
                  <th className="px-4 sm:px-6 py-3.5 font-semibold w-[90px]">Pool Toggle</th>
                  <th className="px-4 sm:px-6 py-3.5 font-semibold">Model Name &amp; ID</th>
                  <th className="px-4 sm:px-6 py-3.5 font-semibold">Provider</th>
                  <th className="px-4 sm:px-6 py-3.5 font-semibold w-[140px] sm:w-[160px]">Quality Override (0-100)</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.06]">
                {filtered.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="px-6 py-12 text-center text-zinc-500 text-xs">
                      No models found matching query filter. Add upstream provider keys or click Refresh Catalog.
                    </td>
                  </tr>
                ) : (
                  filtered.map(m => (
                    <tr
                      key={m.id}
                      className={`hover:bg-white/[0.02] transition-colors ${!m.enabled ? 'opacity-50 bg-rose-500/[0.02]' : ''}`}
                    >
                      <td className="px-4 sm:px-6 py-4">
                        <Switch
                          checked={m.enabled}
                          disabled={busy === m.id}
                          onCheckedChange={(v) => toggleModel(m.id, v)}
                        />
                      </td>
                      <td className="px-4 sm:px-6 py-4 min-w-0">
                        <strong className="text-white text-xs font-semibold block truncate">{m.name}</strong>
                        <div className="text-[11px] text-zinc-500 font-mono mt-0.5 truncate">{m.id}</div>
                      </td>
                      <td className="px-4 sm:px-6 py-4">
                        <Badge variant="accent">{m.provider}</Badge>
                        {!m.enabled && (
                          <Badge variant="err" className="ml-2">Disabled</Badge>
                        )}
                      </td>
                      <td className="px-4 sm:px-6 py-4">
                        <div className="flex items-center gap-2">
                          <input
                            type="number"
                            min={0}
                            max={100}
                            placeholder="Score"
                            className="bg-zinc-950 border border-white/[0.1] text-white px-2.5 py-1.5 rounded-lg text-xs w-[70px] sm:w-[75px] font-mono focus:outline-none focus:border-violet-500"
                            onBlur={e => {
                              if (e.target.value) handleQualityOverride(m.id, e.target.value)
                            }}
                          />
                          <span className="text-[10px] text-zinc-500 font-mono hidden sm:inline">0-100</span>
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </CardBody>
      </Card>
    </div>
  )
}
