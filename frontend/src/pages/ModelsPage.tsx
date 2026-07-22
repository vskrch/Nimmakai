import { useState, useMemo } from 'react'
import { Card, CardBody, Badge, Button, Input, Spinner } from '../components/ui'
import { useCatalog } from '../hooks/useApi'
import { ap, errMsg } from '../lib/api'

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
    // Fallback for older servers without live_ids in /catalog
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
    if (r && (r as Record<string, unknown>).ok !== false) {
      setMsg({ text: 'Catalog refreshed', ok: true })
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
        text: enabled ? `Enabled ${modelId}` : `Disabled ${modelId} (removed from pool)`,
        ok: true,
      })
      reloadCatalog()
    } else {
      setMsg({ text: errMsg(r, 'Failed to update model'), ok: false })
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
        text: enableAll
          ? `Enabled all ${providerId} models`
          : `Disabled all ${providerId} models`,
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
      setMsg({ text: 'Quality must be 0-100', ok: false })
      return
    }
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

  if (!catalog) return <Spinner />

  return (
    <div className="animate-[fadeIn_0.3s_ease]">
      <div className="mb-4">
        <h2 className="text-xl font-semibold">Model Pool</h2>
        <p className="text-zinc-400 text-[13px] mt-1 max-w-[640px]">
          Enable or disable models per provider. Disabled models stay discovered but leave
          routing and <code className="text-zinc-300">/v1/models</code>.
        </p>
      </div>

      <div className="flex gap-3 mb-4 flex-wrap items-center">
        <Input
          placeholder="Search models..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="max-w-[300px]"
        />
        <Button onClick={handleRefresh} disabled={busy === 'refresh'}>
          {busy === 'refresh' ? 'Refreshing…' : 'Refresh Catalog'}
        </Button>
        <button
          type="button"
          onClick={() => setShowDisabledOnly(v => !v)}
          className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition-all ${
            showDisabledOnly
              ? 'bg-amber-500/10 text-amber-200 border-amber-500/30'
              : 'bg-white/[0.03] text-zinc-400 border-white/[0.08]'
          }`}
        >
          Disabled only
        </button>
        <span className="text-xs text-zinc-400">
          {filtered.length} shown · <strong className="text-white">{activeCount}</strong> in pool ·{' '}
          <strong className="text-white">{disabledCount}</strong> disabled
        </span>
      </div>

      {msg && (
        <div
          className={`mb-4 p-3 rounded-lg text-[13px] ${
            msg.ok
              ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20'
              : 'bg-red-500/10 text-red-400 border border-red-500/20'
          }`}
        >
          {msg.text}
          <button className="ml-3 text-xs opacity-60" onClick={() => setMsg(null)}>
            dismiss
          </button>
        </div>
      )}

      <div className="flex flex-wrap gap-2 mb-4">
        <button
          type="button"
          onClick={() => setFilterProv(null)}
          className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all border ${
            !filterProv
              ? 'bg-violet-500/10 text-violet-300 border-violet-500/20'
              : 'bg-white/[0.03] text-zinc-400 border-white/[0.08] hover:bg-white/[0.05]'
          }`}
        >
          All ({allModels.length})
        </button>
        {providers.map(p => {
          const n = allModels.filter(m => m.provider === p).length
          const on = allModels.filter(m => m.provider === p && m.enabled).length
          return (
            <button
              key={p}
              type="button"
              onClick={() => setFilterProv(filterProv === p ? null : p)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all border ${
                filterProv === p
                  ? 'bg-violet-500/10 text-violet-300 border-violet-500/20'
                  : 'bg-white/[0.03] text-zinc-400 border-white/[0.08] hover:bg-white/[0.05]'
              }`}
            >
              {p} ({on}/{n})
            </button>
          )
        })}
      </div>

      {filterProv && (
        <div className="flex gap-2 mb-4">
          <Button
            size="sm"
            onClick={() => bulkProvider(filterProv, true)}
            disabled={busy === `bulk:${filterProv}`}
          >
            Enable all {filterProv}
          </Button>
          <Button
            size="sm"
            variant="danger"
            onClick={() => bulkProvider(filterProv, false)}
            disabled={busy === `bulk:${filterProv}`}
          >
            Disable all {filterProv}
          </Button>
        </div>
      )}

      <Card className="p-0">
        <table className="w-full">
          <thead>
            <tr className="text-left border-b border-white/[0.08]">
              <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px] w-[100px]">
                In pool
              </th>
              <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px]">
                Model
              </th>
              <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px]">
                Provider
              </th>
              <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px] w-[120px]">
                Quality
              </th>
            </tr>
          </thead>
          <tbody>
            {filtered.length === 0 ? (
              <tr>
                <td colSpan={4} className="px-6 py-8 text-center text-zinc-400">
                  No models found. Add providers with keys, then Refresh Catalog.
                </td>
              </tr>
            ) : (
              filtered.map(m => (
                <tr
                  key={m.id}
                  className={`border-b border-white/[0.08] last:border-0 hover:bg-white/[0.01] ${
                    !m.enabled ? 'opacity-60' : ''
                  }`}
                >
                  <td className="px-6 py-3.5">
                    <button
                      type="button"
                      role="switch"
                      aria-checked={m.enabled}
                      disabled={busy === m.id}
                      onClick={() => toggleModel(m.id, !m.enabled)}
                      className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border transition-colors ${
                        m.enabled
                          ? 'bg-emerald-500/80 border-emerald-400/40'
                          : 'bg-zinc-700 border-white/10'
                      } ${busy === m.id ? 'opacity-50' : ''}`}
                      title={m.enabled ? 'Disable (remove from pool)' : 'Enable (add to pool)'}
                    >
                      <span
                        className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow transition ${
                          m.enabled ? 'translate-x-5' : 'translate-x-0.5'
                        } mt-0.5`}
                      />
                    </button>
                  </td>
                  <td className="px-6 py-3.5 text-[13px]">
                    <strong>{m.name}</strong>
                    <div className="text-[11px] text-zinc-500">{m.id}</div>
                  </td>
                  <td className="px-6 py-3.5">
                    <Badge variant="accent">{m.provider}</Badge>
                    {!m.enabled && (
                      <Badge variant="default">Disabled</Badge>
                    )}
                  </td>
                  <td className="px-6 py-3.5">
                    <input
                      type="number"
                      min={0}
                      max={100}
                      placeholder="—"
                      className="bg-black/20 border border-white/[0.08] text-white px-2 py-1 rounded text-[11px] w-[60px] focus:outline-none focus:border-violet-500/50"
                      onBlur={e => {
                        if (e.target.value) handleQualityOverride(m.id, e.target.value)
                      }}
                    />
                    <span className="text-[11px] text-zinc-500 ml-1">0-100</span>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </Card>

      <CardBody className="mt-4 text-xs text-zinc-500">
        Tip: filter by a provider chip, then use Enable/Disable all for that provider.
      </CardBody>
    </div>
  )
}
