import { useState, useEffect, useCallback } from 'react'
import { Card, CardHeader, CardBody, Badge, Button, Input, Skeleton, ErrorState, EmptyState, Switch } from '../components/ui'
import { useCatalog } from '../hooks/useApi'
import { api, errMsg, okBody } from '../lib/api'
import { Filter, Lock, Unlock, Save, Trash2, Search, Shield, X } from 'lucide-react'

interface PoolConfig {
  model_id: string
  allowed_intents: string[]
  excluded_intents: string[]
  allow_auto_router: boolean
  note: string
  updated_at?: number
}

const ALL_INTENTS = [
  'coding_agentic',
  'reasoning',
  'chat_fast',
  'long_horizon',
  'vision',
  'embeddings',
]

export default function ModelPoolGatingPage() {
  const { data: catalog } = useCatalog()
  const [configs, setConfigs] = useState<Record<string, PoolConfig>>({})
  const [search, setSearch] = useState('')
  const [msg, setMsg] = useState<{ text: string; type: 'ok' | 'err' } | null>(null)
  const [saving, setSaving] = useState<string | null>(null)

  const liveIds: string[] = catalog?.live_ids || []

  const loadConfigs = useCallback(async () => {
    const r = await api<{ model_pools: PoolConfig[] }>('/admin/model-pools')
    if (r && Array.isArray(r.model_pools)) {
      const map: Record<string, PoolConfig> = {}
      for (const c of r.model_pools) map[c.model_id] = c
      setConfigs(map)
    }
  }, [])

  useEffect(() => { loadConfigs() }, [loadConfigs])

  const updateConfig = async (modelId: string, patch: Partial<PoolConfig>) => {
    setSaving(modelId)
    const existing = configs[modelId] || {
      model_id: modelId,
      allowed_intents: [],
      excluded_intents: [],
      allow_auto_router: true,
      note: '',
    }
    const next = { ...existing, ...patch }
    const r = await api<{ ok?: boolean; error?: { message?: string } }>(
      `/admin/model-pools/${encodeURIComponent(modelId)}`,
      { method: 'PUT', body: JSON.stringify(next) },
    )
    setSaving(null)
    if (r && okBody(r) && (r as { ok?: boolean }).ok) {
      setConfigs(prev => ({ ...prev, [modelId]: next }))
      setMsg({ text: `Updated pool gating for ${modelId}`, type: 'ok' })
    } else {
      setMsg({ text: errMsg(r, 'Save failed'), type: 'err' })
    }
  }

  const deleteConfig = async (modelId: string) => {
    const r = await api<{ ok?: boolean }>(`/admin/model-pools/${encodeURIComponent(modelId)}`, {
      method: 'DELETE',
    })
    if (r && (r as { ok?: boolean }).ok) {
      setConfigs(prev => {
        const next = { ...prev }
        delete next[modelId]
        return next
      })
      setMsg({ text: `Reset ${modelId} to unrestricted pooling`, type: 'ok' })
    }
  }

  const toggleIntent = (modelId: string, intent: string, list: 'allowed' | 'excluded') => {
    const cfg = configs[modelId]
    const key = list === 'allowed' ? 'allowed_intents' : 'excluded_intents'
    const otherKey = list === 'allowed' ? 'excluded_intents' : 'allowed_intents'
    const current = cfg?.[key] || []
    const next = current.includes(intent)
      ? current.filter(i => i !== intent)
      : [...current, intent]
    // Clear the opposite list when setting one (mutually exclusive)
    updateConfig(modelId, { [key]: next, [otherKey]: [] } as Partial<PoolConfig>)
  }

  const filteredModels = liveIds.filter(m =>
    search === '' || m.toLowerCase().includes(search.toLowerCase())
  )

  if (!catalog) return (
    <div className="space-y-6 animate-[fadeIn_0.25s_ease-out]">
      <div className="flex items-center gap-2">
        <Filter className="w-5 h-5 text-violet-400" />
        <h2 className="text-lg font-bold text-white tracking-tight">Model Pool Gating</h2>
      </div>
      <Skeleton lines={3} className="max-w-xl" />
      <Skeleton cards={2} />
    </div>
  )

  return (
    <div className="space-y-6 animate-[fadeIn_0.25s_ease-out]">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <div className="flex items-center gap-2">
            <Filter className="w-5 h-5 text-violet-400" />
            <h2 className="text-lg font-bold text-white tracking-tight">Model Pool Gating</h2>
          </div>
          <p className="text-zinc-400 text-xs mt-1 max-w-[640px]">
            Control which models participate in which intent pools. Exclude expensive frontier
            models from <code className="text-amber-300/90 font-mono">potato/auto</code> while
            keeping them available for custom ladders. Custom ladders bypass auto-router gating —
            only <code className="text-violet-300/90 font-mono">allow_auto_router</code> and intent
            lists affect the generic auto pool.
          </p>
        </div>
      </div>

      {msg && (
        <div className={`p-4 rounded-xl text-xs flex justify-between items-center font-medium ${
          msg.type === 'ok'
            ? 'bg-emerald-500/10 text-emerald-300 border border-emerald-500/20'
            : 'bg-rose-500/10 text-rose-300 border border-rose-500/20'}`}>
          <span>{msg.text}</span>
          <button className="text-zinc-400 hover:text-white" onClick={() => setMsg(null)}>Dismiss</button>
        </div>
      )}

      {/* Search */}
      <div className="max-w-sm">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-zinc-500" />
          <Input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Filter models…"
            className="pl-9"
          />
        </div>
      </div>

      {/* Model list */}
      <div className="space-y-3">
        {filteredModels.map(modelId => {
          const cfg = configs[modelId]
          const isGated = cfg && (
            !cfg.allow_auto_router ||
            cfg.allowed_intents.length > 0 ||
            cfg.excluded_intents.length > 0
          )
          return (
            <Card key={modelId}>
              <CardHeader>
                <div className="flex items-center justify-between gap-3 flex-wrap">
                  <div className="flex items-center gap-3 min-w-0">
                    <code className="font-mono text-sm font-bold text-zinc-100 truncate">{modelId}</code>
                    {cfg && !cfg.allow_auto_router && (
                      <Badge variant="warn">
                        <Lock className="w-3 h-3 inline mr-1" />
                        Excluded from auto
                      </Badge>
                    )}
                    {cfg?.allowed_intents.length ? (
                      <Badge variant="accent">
                        Allowed: {cfg.allowed_intents.join(', ')}
                      </Badge>
                    ) : null}
                    {cfg?.excluded_intents.length ? (
                      <Badge variant="warn">
                        Excluded: {cfg.excluded_intents.join(', ')}
                      </Badge>
                    ) : null}
                    {!isGated && <Badge variant="ok">Unrestricted</Badge>}
                  </div>
                  <div className="flex items-center gap-2">
                    {isGated && (
                      <Button
                        size="xs"
                        variant="ghost"
                        onClick={() => deleteConfig(modelId)}
                        disabled={saving === modelId}
                        className="text-zinc-400 hover:text-rose-400"
                      >
                        <Unlock className="w-3 h-3" />
                        Reset
                      </Button>
                    )}
                  </div>
                </div>
              </CardHeader>
              <CardBody className="space-y-3">
                {/* Auto-router toggle */}
                <div className="flex items-center justify-between p-3 rounded-lg bg-zinc-950/60 border border-white/[0.06]">
                  <div className="flex items-center gap-2">
                    <Shield className={`w-4 h-4 ${cfg && !cfg.allow_auto_router ? 'text-amber-400' : 'text-emerald-400'}`} />
                    <div>
                      <p className="text-[12px] font-medium text-zinc-200">Allow in potato/auto pool</p>
                      <p className="text-[10px] text-zinc-500">
                        When off, this model is excluded from the generic auto-router but still
                        available for custom ladders.
                      </p>
                    </div>
                  </div>
                  <Switch
                    checked={cfg?.allow_auto_router ?? true}
                    onCheckedChange={(v) => updateConfig(modelId, { allow_auto_router: v })}
                    disabled={saving === modelId}
                  />
                </div>

                {/* Intent gating */}
                <div>
                  <div className="text-[10px] uppercase tracking-wider text-zinc-500 font-semibold mb-2">
                    Intent gating (mutually exclusive — set allowed OR excluded, not both)
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {ALL_INTENTS.map(intent => {
                      const isAllowed = cfg?.allowed_intents.includes(intent)
                      const isExcluded = cfg?.excluded_intents.includes(intent)
                      return (
                        <button
                          key={intent}
                          onClick={() => toggleIntent(modelId, intent, isAllowed ? 'allowed' : 'allowed')}
                          onContextMenu={e => { e.preventDefault(); toggleIntent(modelId, intent, isExcluded ? 'excluded' : 'excluded') }}
                          className={`px-2.5 py-1 rounded-lg text-[11px] font-mono border transition-all ${
                            isAllowed
                              ? 'bg-emerald-500/15 border-emerald-500/30 text-emerald-300'
                              : isExcluded
                                ? 'bg-rose-500/15 border-rose-500/30 text-rose-300'
                                : 'bg-white/[0.02] border-white/[0.06] text-zinc-400 hover:border-white/15'
                          }`}
                          title={`Left-click: allow only this intent · Right-click: exclude this intent`}
                        >
                          {intent}
                        </button>
                      )
                    })}
                  </div>
                  <p className="text-[10px] text-zinc-600 mt-1.5">
                    Left-click = add to <span className="text-emerald-400">allowed</span> (whitelist) ·
                    Right-click = add to <span className="text-rose-400">excluded</span> (blacklist)
                  </p>
                </div>

                {/* Note */}
                <div>
                  <label className="text-[10px] uppercase tracking-wider text-zinc-500 font-semibold block mb-1">
                    Note (optional)
                  </label>
                  <Input
                    value={cfg?.note || ''}
                    onChange={e => updateConfig(modelId, { note: e.target.value })}
                    placeholder="e.g. 'frontier model — reserve for coding ladder'"
                  />
                </div>
              </CardBody>
            </Card>
          )
        })}

        {filteredModels.length === 0 && (
          <EmptyState title={liveIds.length === 0 ? 'No live models in catalog' : 'No models match your filter'}>
            {liveIds.length === 0
              ? 'Refresh the catalog first to populate the pool.'
              : 'Try adjusting your search query.'}
          </EmptyState>
        )}
      </div>
    </div>
  )
}