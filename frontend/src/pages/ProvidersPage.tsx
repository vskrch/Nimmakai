import React, { useState } from 'react'
import { Card, CardHeader, CardBody, Badge, Button, Input, Spinner, StatusDot } from '../components/ui'
import { useProviders } from '../hooks/useApi'
import { ad, ap, errMsg } from '../lib/api'
import {
  Server,
  Plus,
  RefreshCw,
  Key,
  Cpu,
  CheckCircle2,
  Trash2,
  Play,
  X,
  Zap,
  Sparkles,
  ExternalLink
} from 'lucide-react'

export default function ProvidersPage() {
  const { data, reload } = useProviders()
  const [showAdd, setShowAdd] = useState(false)
  const [form, setForm] = useState({ id: '', name: '', base_url: '', api_keys: '', rpm_limit: 40, rpd_limit: 2000 })
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null)

  if (!data) return <Spinner />

  const presets = data.presets || []
  const providers = data.providers || []
  const pool = data.pool || { live_models: 0, active_providers: 0, models_by_provider: {} }

  async function handleAdd() {
    if (!form.id || !form.base_url) { setMsg({ text: 'ID and Base URL required', ok: false }); return }
    const keys = form.api_keys ? form.api_keys.split(',').map(s => s.trim()).filter(Boolean) : []
    const isEdit = providers.some(p => p.id === form.id)
    if (!isEdit && keys.length === 0) {
      setMsg({ text: 'Paste at least one API key (OpenCode Zen uses key from opencode.ai/auth)', ok: false })
      return
    }
    setSaving(true)
    const payload: Record<string, unknown> = {
      ...form,
      api_keys: keys,
    }
    if (keys.length > 0 || !isEdit) {
      payload.enabled = true
    }
    const r = await ap('/admin/providers', payload)
    setSaving(false)
    if (r && (r as Record<string, unknown>).ok) {
      setMsg({ text: (r as Record<string, unknown>).message as string || 'Provider credentials saved', ok: true })
      setShowAdd(false)
      setForm({ id: '', name: '', base_url: '', api_keys: '', rpm_limit: 40, rpd_limit: 2000 })
      reload()
    } else {
      setMsg({ text: errMsg(r, 'Failed to save provider'), ok: false })
    }
  }

  async function handleTest(pid: string) {
    setMsg({ text: 'Testing provider endpoint...', ok: true })
    const keys = form.api_keys ? form.api_keys.split(',').map(s => s.trim()).filter(Boolean) : []
    const r = await ap('/admin/providers/test', {
      id: pid || undefined,
      base_url: form.base_url || undefined,
      api_keys: keys,
    })
    setMsg({ text: (r as Record<string, unknown>)?.message as string || 'Test complete', ok: !!(r as Record<string, unknown>)?.ok })
  }

  async function handleDelete(pid: string) {
    const r = await ad(`/admin/providers/${pid}`)
    if (r && (r as Record<string, unknown>).ok !== false) {
      reload()
      setMsg({ text: 'Provider removed from gateway configuration', ok: true })
    } else {
      setMsg({ text: errMsg(r, 'Delete failed'), ok: false })
    }
  }

  return (
    <div className="space-y-6 animate-[fadeIn_0.25s_ease-out]">
      {/* Header controls */}
      <div className="flex justify-between items-start gap-4 flex-wrap">
        <div>
          <div className="flex items-center gap-2">
            <Server className="w-5 h-5 text-violet-400" />
            <h2 className="text-lg font-bold text-white tracking-tight">LLM Endpoint Providers</h2>
          </div>
          <p className="text-zinc-400 text-xs mt-1 max-w-[600px]">
            Connect OpenAI-compatible API providers (OpenCode, Groq, Cerebras, OpenRouter, Gemini, etc.). Keys are securely stored in SQLite and merged into the active routing ladder.
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" onClick={reload}>
            <RefreshCw className="w-3.5 h-3.5" />
            <span>Refresh Pool</span>
          </Button>
          <Button variant="primary" onClick={() => setShowAdd(true)}>
            <Plus className="w-4 h-4" />
            <span>Custom Endpoint</span>
          </Button>
        </div>
      </div>

      {msg && (
        <div className={`p-4 rounded-xl text-xs flex items-center justify-between font-medium ${
          msg.ok ? 'bg-emerald-500/10 text-emerald-300 border border-emerald-500/20' : 'bg-rose-500/10 text-rose-300 border border-rose-500/20'
        }`}>
          <span>{msg.text}</span>
          <button className="text-zinc-400 hover:text-white" onClick={() => setMsg(null)}>Dismiss</button>
        </div>
      )}

      {/* Pool Overview Badges */}
      <div className="flex flex-wrap gap-2.5">
        <div className="bg-zinc-900 border border-white/[0.08] rounded-xl px-3.5 py-2 text-xs text-zinc-300 flex items-center gap-2">
          <Cpu className="w-3.5 h-3.5 text-violet-400" />
          <span><strong className="text-white">{pool.live_models ?? 0}</strong> live models in pool</span>
        </div>
        <div className="bg-zinc-900 border border-white/[0.08] rounded-xl px-3.5 py-2 text-xs text-zinc-300 flex items-center gap-2">
          <Server className="w-3.5 h-3.5 text-emerald-400" />
          <span><strong className="text-white">{pool.active_providers ?? 0}</strong> active providers</span>
        </div>
        {Object.keys(pool.models_by_provider || {}).sort().map(pid => (
          <div key={pid} className="bg-zinc-900 border border-white/[0.08] rounded-xl px-3.5 py-2 text-xs text-zinc-400 font-mono">
            <span className="text-white font-semibold">{pid}:</span> {pool.models_by_provider[pid]} models
          </div>
        ))}
      </div>

      {/* Quick Setup Presets */}
      {presets.length > 0 && (
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-violet-400" />
              <h3 className="text-sm font-semibold text-white">Popular & Free Provider Presets</h3>
            </div>
            <span className="text-xs text-zinc-400">Click any card to populate configuration</span>
          </CardHeader>
          <CardBody>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
              {presets.map(p => (
                <button
                  key={p.id}
                  onClick={() => { setForm(f => ({ ...f, id: p.id, name: p.name, base_url: p.base_url })); setShowAdd(true) }}
                  className={`bg-zinc-950/60 border border-white/[0.08] rounded-2xl p-4 text-left transition-all duration-200 hover:border-violet-500/50 hover:bg-violet-500/[0.04] flex flex-col justify-between gap-3 group relative overflow-hidden ${
                    p.already_configured ? 'border-emerald-500/30 bg-emerald-500/[0.02]' : ''
                  }`}
                >
                  <div>
                    <div className="flex items-center justify-between gap-2 mb-1">
                      <h4 className="text-xs font-bold text-white group-hover:text-violet-300 transition-colors">{p.name}</h4>
                      {p.already_configured && <Badge variant="ok">Configured</Badge>}
                    </div>
                    <p className="text-[11px] text-zinc-400 font-mono truncate">{p.base_url}</p>
                  </div>
                  <div className="flex flex-wrap gap-1.5 pt-2 border-t border-white/[0.06]">
                    {p.free_tier && <Badge variant="free">Free Tier</Badge>}
                    {p.speed_tier === 'ultra' && <Badge variant="fast">Ultra TPS</Badge>}
                    {p.speed_tier === 'fast' && <Badge variant="fast">Fast</Badge>}
                  </div>
                </button>
              ))}
            </div>
          </CardBody>
        </Card>
      )}

      {/* Add / Edit Form Drawer Card */}
      {showAdd && (
        <Card className="border-violet-500/40 shadow-[0_0_30px_rgba(139,92,246,0.15)]">
          <CardHeader>
            <div className="flex items-center gap-2">
              <Plus className="w-4 h-4 text-violet-400" />
              <h3 className="text-sm font-semibold text-white">Configure Provider Endpoint</h3>
            </div>
            <button className="text-zinc-400 hover:text-white p-1 rounded-lg" onClick={() => setShowAdd(false)}>
              <X className="w-4 h-4" />
            </button>
          </CardHeader>
          <CardBody className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <label className="block text-xs font-semibold text-zinc-300 mb-1.5">Provider Unique ID</label>
                <Input value={form.id} onChange={e => setForm(f => ({ ...f, id: e.target.value }))} placeholder="e.g. groq or opencode" />
              </div>
              <div>
                <label className="block text-xs font-semibold text-zinc-300 mb-1.5">Display Name</label>
                <Input value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} placeholder="e.g. Groq Inference Engine" />
              </div>
            </div>
            <div>
              <label className="block text-xs font-semibold text-zinc-300 mb-1.5">Base URL (OpenAI-compatible /v1 Root)</label>
              <Input value={form.base_url} onChange={e => setForm(f => ({ ...f, base_url: e.target.value }))} placeholder="https://api.groq.com/openai/v1" />
            </div>
            <div>
              <label className="block text-xs font-semibold text-zinc-300 mb-1.5">API Keys (Comma-separated for multi-key round robin)</label>
              <Input value={form.api_keys} onChange={e => setForm(f => ({ ...f, api_keys: e.target.value }))} placeholder="Paste API keys from provider console..." />
              {form.id === 'zen' && (
                <p className="text-[11px] text-zinc-400 mt-2 font-mono bg-violet-500/10 p-2.5 rounded-xl border border-violet-500/20">
                  OpenCode Zen uses the standard API key from <a href="https://opencode.ai/auth" target="_blank" rel="noreferrer" className="text-violet-300 underline inline-flex items-center gap-1">opencode.ai/auth <ExternalLink className="w-3 h-3" /></a>.
                </p>
              )}
            </div>
            <div className="flex gap-2 justify-end pt-2">
              <Button variant="default" onClick={() => setShowAdd(false)}>Cancel</Button>
              <Button variant="secondary" onClick={() => handleTest(form.id)}>
                <Play className="w-3.5 h-3.5" />
                <span>Test Connection</span>
              </Button>
              <Button variant="primary" onClick={handleAdd} disabled={saving}>
                <CheckCircle2 className="w-3.5 h-3.5" />
                <span>{saving ? 'Saving…' : 'Save & Join Pool'}</span>
              </Button>
            </div>
          </CardBody>
        </Card>
      )}

      {/* Configured Provider Table */}
      <div>
        <h3 className="text-xs font-semibold uppercase tracking-wider text-zinc-400 mb-3">Configured Upstream Providers</h3>
        <Card>
          <CardBody className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-white/[0.08] text-[10px] uppercase tracking-wider text-zinc-400 bg-white/[0.01]">
                    <th className="px-6 py-3.5 font-semibold">Provider Name</th>
                    <th className="px-6 py-3.5 font-semibold">Base URL</th>
                    <th className="px-6 py-3.5 font-semibold">Active Keys</th>
                    <th className="px-6 py-3.5 font-semibold">Live Models</th>
                    <th className="px-6 py-3.5 font-semibold">Status</th>
                    <th className="px-6 py-3.5 font-semibold">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/[0.06]">
                  {providers.map(p => {
                    const active = p.runtime || (p.enabled && (p.key_count || 0) > 0)

                    return (
                      <tr key={p.id} className="hover:bg-white/[0.02] transition-colors">
                        <td className="px-6 py-4">
                          <div className="flex items-center gap-2">
                            <strong className="text-white text-xs">{p.name}</strong>
                            {p.free_tier && <Badge variant="free">Free</Badge>}
                            {(p.speed_tier === 'ultra' || p.speed_tier === 'fast') && <Badge variant="fast">{p.speed_tier === 'ultra' ? 'Ultra' : 'Fast'}</Badge>}
                          </div>
                          <div className="text-[11px] text-zinc-500 font-mono mt-0.5">{p.id}</div>
                        </td>
                        <td className="px-6 py-4 font-mono text-zinc-300 max-w-[220px] truncate" title={p.base_url}>{p.base_url}</td>
                        <td className="px-6 py-4 text-zinc-300 font-mono">
                          {p.key_count ?? 0} keys
                          {p.available_keys != null && <span className="text-emerald-400 ml-1">({p.available_keys} ready)</span>}
                        </td>
                        <td className="px-6 py-4 font-mono text-violet-300 font-semibold">{p.model_count ?? 0} models</td>
                        <td className="px-6 py-4">
                          <Badge variant={active ? 'ok' : !p.enabled ? 'default' : 'err'}>
                            <StatusDot ok={!!active} />
                            {!p.enabled ? 'Disabled' : active ? 'Active in Pool' : 'No Active Keys'}
                          </Badge>
                        </td>
                        <td className="px-6 py-4">
                          <div className="flex gap-2">
                            <Button size="xs" variant="default" onClick={() => { setForm({ id: p.id, name: p.name, base_url: p.base_url, api_keys: '', rpm_limit: p.rpm_limit, rpd_limit: p.rpd_limit }); setShowAdd(true) }}>Edit</Button>
                            <Button size="xs" variant="secondary" onClick={() => handleTest(p.id)}>Test</Button>
                            {!p.builtin && (
                              <Button size="xs" variant="danger" onClick={() => handleDelete(p.id)}>
                                <Trash2 className="w-3 h-3" />
                              </Button>
                            )}
                          </div>
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
    </div>
  )
}
