import React, { useState, useEffect, useCallback } from 'react'
import { Card, CardHeader, CardBody, Badge, Button, Spinner, Input } from '../components/ui'
import { useCatalog } from '../hooks/useApi'
import { api, ap, ad, errMsg, okBody } from '../lib/api'
import { GitFork, Plus, Trash2, X, ArrowRight, Save, RotateCcw, Lock } from 'lucide-react'

// Virtual router ids that may carry a custom ladder.
// `nimmakai/auto` is deliberately excluded — it stays on the intelligent router.
const ROUTABLE_MODELS: { id: string; label: string; desc: string }[] = [
  { id: 'nimmakai/coding', label: 'nimmakai/coding', desc: 'Coding & agentic operations' },
  { id: 'nimmakai/auto-coding', label: 'nimmakai/auto-coding', desc: 'Auto-coding alias' },
  { id: 'nimmakai/best', label: 'nimmakai/best', desc: 'Best / frontier alias' },
  { id: 'nimmakai/auto-fast', label: 'nimmakai/auto-fast', desc: 'Latency-first' },
  { id: 'nimmakai/auto-cheap', label: 'nimmakai/auto-cheap', desc: 'Cost-aware' },
]

interface ModelLadder {
  model_id: string
  chain: string[]
  note?: string
  updated_at?: number
}

export default function ModelLaddersPage() {
  const { data: catalog } = useCatalog()
  const [ladders, setLadders] = useState<ModelLadder[]>([])
  const [selectedModel, setSelectedModel] = useState<string>('nimmakai/coding')
  const [chain, setChain] = useState<string[]>([])
  const [note, setNote] = useState('')
  const [search, setSearch] = useState('')
  const [dragIdx, setDragIdx] = useState<number | null>(null)
  const [dropIdx, setDropIdx] = useState<number | null>(null)
  const [poolDragModel, setPoolDragModel] = useState<string | null>(null)
  const [msg, setMsg] = useState<{ text: string; type: 'ok' | 'err' } | null>(null)
  const [saving, setSaving] = useState(false)

  const liveIds: string[] = catalog?.live_ids || []
  const liveSet = new Set(liveIds.map(m => m.toLowerCase()))

  const loadLadders = useCallback(async () => {
    const r = await api<{ ladders: ModelLadder[] }>('/admin/model-ladders')
    if (r) setLadders(r.ladders)
  }, [])

  useEffect(() => { loadLadders() }, [loadLadders])

  // Load selected model's existing chain into the editor
  useEffect(() => {
    const existing = ladders.find(l => l.model_id === selectedModel)
    if (existing) {
      setChain(existing.chain)
      setNote(existing.note || '')
    } else {
      setChain([])
      setNote('')
    }
  }, [selectedModel, ladders])

  const filteredPool = liveIds.filter(m =>
    !chain.some(c => c.toLowerCase() === m.toLowerCase()) &&
    (search === '' || m.toLowerCase().includes(search.toLowerCase()))
  )

  // ── Drag-and-drop chain reordering ───────────────────────────────
  const onChainDragStart = (idx: number) => setDragIdx(idx)
  const onChainDragOver = (e: React.DragEvent, idx: number) => {
    e.preventDefault()
    setDropIdx(idx)
  }
  const onChainDrop = (idx: number) => {
    if (dragIdx === null || dragIdx === idx) { setDragIdx(null); setDropIdx(null); return }
    const next = [...chain]
    const [moved] = next.splice(dragIdx, 1)
    next.splice(idx, 0, moved)
    setChain(next)
    setDragIdx(null)
    setDropIdx(null)
  }

  // ── Drop from pool onto chain ─────────────────────────────────────
  const onChainAreaDragOver = (e: React.DragEvent) => {
    if (poolDragModel) e.preventDefault()
  }
  const onChainAreaDrop = (e: React.DragEvent) => {
    e.preventDefault()
    if (poolDragModel && !chain.some(c => c.toLowerCase() === poolDragModel.toLowerCase())) {
      setChain([...chain, poolDragModel])
    }
    setPoolDragModel(null)
  }

  const removeFromChain = (idx: number) => setChain(chain.filter((_, i) => i !== idx))

  const handleSave = async () => {
    setSaving(true)
    const r = await ap<{ ok?: boolean; ladder?: ModelLadder; error?: { message?: string } }>(
      '/admin/model-ladders',
      { model_id: selectedModel, chain, note }
    )
    setSaving(false)
    if (r && okBody(r) && (r as { ok?: boolean }).ok) {
      setMsg({ text: `Saved custom ladder for ${selectedModel}`, type: 'ok' })
      loadLadders()
    } else {
      setMsg({ text: errMsg(r, 'Save failed'), type: 'err' })
    }
  }

  const handleClear = async () => {
    const r = await ap<{ ok?: boolean }>('/admin/model-ladders', { model_id: selectedModel, chain: [] })
    if (r && (r as { ok?: boolean }).ok) {
      setMsg({ text: `Cleared custom ladder for ${selectedModel}`, type: 'ok' })
      setChain([])
      setNote('')
      loadLadders()
    }
  }

  const handleDelete = async (modelId: string) => {
    const r = await ad<{ ok?: boolean }>(`/admin/model-ladders/${encodeURIComponent(modelId)}`)
    if (r && (r as { ok?: boolean }).ok) {
      setMsg({ text: `Deleted ladder for ${modelId}`, type: 'ok' })
      loadLadders()
    }
  }

  if (!catalog) return <Spinner />

  const selectedMeta = ROUTABLE_MODELS.find(m => m.id === selectedModel)
  const ladderForSelected = ladders.find(l => l.model_id === selectedModel)

  return (
    <div className="space-y-6 animate-[fadeIn_0.25s_ease-out]">
      <div className="flex justify-between items-start gap-4 flex-wrap">
        <div>
          <div className="flex items-center gap-2">
            <GitFork className="w-5 h-5 text-violet-400" />
            <h2 className="text-lg font-bold text-white tracking-tight">Custom Model Ladders</h2>
          </div>
          <p className="text-zinc-400 text-xs mt-1 max-w-[620px]">
            Drag models from the pool to build a custom fallback ladder for a virtual model id.
            When a client requests that model, the chain runs A → B → C (first available wins).
            <span className="text-amber-300/80"> Default </span>
            <code className="text-amber-300/90 font-mono">nimmakai/auto</code>
            <span className="text-amber-300/80"> is never overridden — it stays on the intelligent router.</span>
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

      <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-6">
        {/* Model selector + saved ladders */}
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <h3 className="text-sm font-semibold text-white">Virtual Models</h3>
            </CardHeader>
            <CardBody className="p-2 space-y-1">
              {ROUTABLE_MODELS.map(m => {
                const has = ladders.some(l => l.model_id === m.id)
                const isActive = selectedModel === m.id
                return (
                  <button
                    key={m.id}
                    onClick={() => setSelectedModel(m.id)}
                    className={`w-full text-left px-3 py-2.5 rounded-xl transition-all border ${
                      isActive
                        ? 'bg-violet-500/15 border-violet-500/30 text-violet-100'
                        : 'border-transparent text-zinc-300 hover:bg-white/[0.04]'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <code className="font-mono text-xs font-semibold">{m.label}</code>
                      {has && <Badge variant="ok">Custom</Badge>}
                    </div>
                    <p className="text-[10px] text-zinc-500 mt-0.5">{m.desc}</p>
                  </button>
                )
              })}
              <div className="pt-2 mt-2 border-t border-white/[0.06]">
                <div className="flex items-center gap-1.5 px-3 py-1.5 text-[10px] text-zinc-500">
                  <Lock className="w-3 h-3" />
                  <code className="font-mono">nimmakai/auto</code>
                  <span className="text-zinc-600">— locked (intelligent router)</span>
                </div>
              </div>
            </CardBody>
          </Card>

          {ladders.length > 0 && (
            <Card>
              <CardHeader>
                <h3 className="text-sm font-semibold text-white">Saved Ladders ({ladders.length})</h3>
              </CardHeader>
              <CardBody className="p-2 space-y-1">
                {ladders.map(l => (
                  <div key={l.model_id} className="flex items-center justify-between gap-2 px-3 py-2 rounded-lg bg-white/[0.02] border border-white/[0.04]">
                    <div className="min-w-0">
                      <code className="font-mono text-xs text-zinc-200 truncate block">{l.model_id}</code>
                      <span className="text-[10px] text-zinc-500">{l.chain.length} models</span>
                    </div>
                    <button
                      onClick={() => handleDelete(l.model_id)}
                      className="p-1.5 text-zinc-500 hover:text-rose-400 hover:bg-rose-500/10 rounded-lg transition-colors shrink-0"
                      title="Delete ladder"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                ))}
              </CardBody>
            </Card>
          )}
        </div>

        {/* Editor: pool → canvas */}
        <div className="space-y-4">
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between gap-3 flex-wrap">
                <div className="flex items-center gap-2.5">
                  <code className="font-mono text-sm font-bold text-violet-200">{selectedModel}</code>
                  <Badge variant="accent">{selectedMeta?.desc}</Badge>
                  {ladderForSelected && <Badge variant="ok">Editing saved ladder</Badge>}
                </div>
                <div className="flex items-center gap-2">
                  <Button size="sm" variant="danger" onClick={handleClear} disabled={!chain.length}>
                    <RotateCcw className="w-3.5 h-3.5" />
                    <span>Revert to Auto</span>
                  </Button>
                  <Button size="sm" variant="primary" onClick={handleSave} disabled={saving || !chain.length}>
                    <Save className="w-3.5 h-3.5" />
                    <span>{saving ? 'Saving…' : 'Save Ladder'}</span>
                  </Button>
                </div>
              </div>
            </CardHeader>
            <CardBody className="space-y-4">
              {/* Canvas: the ordered fallback chain */}
              <div
                onDragOver={onChainAreaDragOver}
                onDrop={onChainAreaDrop}
                className={`min-h-[160px] rounded-xl border-2 border-dashed transition-all p-3 ${
                  dropIdx !== null || poolDragModel
                    ? 'border-violet-500/50 bg-violet-500/5'
                    : 'border-white/[0.1] bg-zinc-950/40'
                }`}
              >
                <div className="flex items-center justify-between mb-2">
                  <span className="text-[10px] uppercase tracking-wider text-zinc-500 font-semibold">
                    Fallback Chain ({chain.length})
                  </span>
                  {chain.length > 0 && (
                    <span className="text-[10px] text-zinc-500">
                      Drag to reorder · drop models here from the pool
                    </span>
                  )}
                </div>

                {chain.length === 0 ? (
                  <div className="py-10 text-center text-xs text-zinc-500">
                    Drag models from the pool below to build the fallback chain.
                    <br />
                    <span className="text-zinc-600">First available model wins; later entries are fallbacks.</span>
                  </div>
                ) : (
                  <ol className="space-y-2">
                    {chain.map((m, i) => (
                      <li
                        key={`${m}-${i}`}
                        draggable
                        onDragStart={() => onChainDragStart(i)}
                        onDragOver={(e) => onChainDragOver(e, i)}
                        onDrop={() => onChainDrop(i)}
                        onDragEnd={() => { setDragIdx(null); setDropIdx(null) }}
                        className={`flex items-center gap-3 px-3 py-2.5 rounded-lg border cursor-grab active:cursor-grabbing transition-all ${
                          dropIdx === i
                            ? 'border-violet-500/60 bg-violet-500/10'
                            : dragIdx === i
                              ? 'border-white/20 bg-white/[0.06] opacity-60'
                              : 'border-white/[0.08] bg-white/[0.02] hover:border-white/[0.15]'
                        }`}
                      >
                        <span className="w-6 h-6 rounded-md bg-violet-500/15 border border-violet-500/30 text-violet-200 text-xs font-mono font-bold flex items-center justify-center shrink-0">
                          {i + 1}
                        </span>
                        <code className="font-mono text-xs text-zinc-100 flex-1 min-w-0 truncate">{m}</code>
                        {liveSet.has(m.toLowerCase())
                          ? <Badge variant="ok">live</Badge>
                          : <Badge variant="warn">offline</Badge>}
                        {i === 0 && <Badge variant="accent">primary</Badge>}
                        <button
                          onClick={() => removeFromChain(i)}
                          className="p-1 text-zinc-500 hover:text-rose-400 hover:bg-rose-500/10 rounded transition-colors shrink-0"
                        >
                          <X className="w-3.5 h-3.5" />
                        </button>
                      </li>
                    ))}
                  </ol>
                )}
              </div>

              {/* Note */}
              <div>
                <label className="text-[10px] uppercase tracking-wider text-zinc-500 font-semibold block mb-1.5">
                  Note (optional)
                </label>
                <Input
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="Why this ladder? e.g. 'coding-only pool, groq first for speed'"
                />
              </div>
            </CardBody>
          </Card>

          {/* Model pool */}
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between gap-3 flex-wrap">
                <h3 className="text-sm font-semibold text-white">
                  Model Pool ({filteredPool.length})
                  <span className="text-zinc-500 font-normal ml-2">drag → canvas</span>
                </h3>
                <div className="w-64">
                  <Input
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="Filter models…"
                  />
                </div>
              </div>
            </CardHeader>
            <CardBody className="p-3">
              {filteredPool.length === 0 ? (
                <p className="text-center text-xs text-zinc-500 py-6">
                  {liveIds.length === 0
                    ? 'No live models in catalog. Refresh the catalog first.'
                    : 'All live models are already in the chain, or no matches for your filter.'}
                </p>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-2 max-h-[420px] overflow-y-auto custom-scrollbar pr-1">
                  {filteredPool.slice(0, 300).map(m => (
                    <div
                      key={m}
                      draggable
                      onDragStart={() => setPoolDragModel(m)}
                      onDragEnd={() => setPoolDragModel(null)}
                      className="flex items-center gap-2 px-3 py-2 rounded-lg border border-white/[0.06] bg-white/[0.02] hover:border-violet-500/40 hover:bg-violet-500/5 cursor-grab active:cursor-grabbing transition-all group"
                    >
                      <span className="w-1.5 h-1.5 rounded-full bg-zinc-600 group-hover:bg-violet-400 transition-colors shrink-0" />
                      <code className="font-mono text-[11px] text-zinc-300 flex-1 min-w-0 truncate">{m}</code>
                      <Plus className="w-3 h-3 text-zinc-600 group-hover:text-violet-400 transition-colors shrink-0" />
                    </div>
                  ))}
                </div>
              )}
              {filteredPool.length > 300 && (
                <p className="text-[10px] text-zinc-500 mt-2 text-center">
                  Showing first 300 — refine your filter to find more.
                </p>
              )}
            </CardBody>
          </Card>
        </div>
      </div>
    </div>
  )
}