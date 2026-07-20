import { useState } from 'react'
import { Card, CardHeader, CardBody, Badge, Button, Input, Spinner, StatusDot } from '../components/ui'
import { useProviders } from '../hooks/useApi'
import { ap, errMsg } from '../lib/api'

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
    setSaving(true)
    const r = await ap('/admin/providers', {
      ...form,
      api_keys: form.api_keys ? form.api_keys.split(',').map(s => s.trim()).filter(Boolean) : [],
    })
    setSaving(false)
    if (r && (r as Record<string, unknown>).ok) {
      setMsg({ text: (r as Record<string, unknown>).message as string || 'Saved', ok: true })
      setShowAdd(false)
      setForm({ id: '', name: '', base_url: '', api_keys: '', rpm_limit: 40, rpd_limit: 2000 })
      reload()
    } else {
      setMsg({ text: errMsg(r, 'Failed'), ok: false })
    }
  }

  async function handleTest(pid: string) {
    setMsg({ text: 'Testing...', ok: true })
    const r = await ap('/admin/providers/test', { id: pid })
    setMsg({ text: (r as Record<string, unknown>)?.message as string || 'Test complete', ok: !!(r as Record<string, unknown>)?.ok })
  }

  async function handleDelete(pid: string) {
    const r = await ap(`/admin/providers/${pid}`, undefined)
    if (r) { reload(); setMsg({ text: 'Deleted', ok: true }) }
  }

  return (
    <div className="animate-[fadeIn_0.3s_ease]">
      <div className="flex justify-between items-start mb-6 gap-4 flex-wrap">
        <div>
          <h2 className="text-xl font-semibold">Providers</h2>
          <p className="text-zinc-400 text-[13px] mt-1 max-w-[560px]">
            Add OpenAI-compatible endpoints. Keys stored in local SQLite. Every provider's models join the shared pool.
          </p>
        </div>
        <div className="flex gap-2">
          <Button onClick={reload}>Refresh Pool</Button>
          <Button variant="primary" onClick={() => setShowAdd(true)}>+ Custom Endpoint</Button>
        </div>
      </div>

      {msg && (
        <div className={`mb-4 p-3 rounded-lg text-[13px] ${msg.ok ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-red-500/10 text-red-400 border border-red-500/20'}`}>
          {msg.text}
          <button className="ml-3 text-xs opacity-60" onClick={() => setMsg(null)}>dismiss</button>
        </div>
      )}

      <div className="flex flex-wrap gap-2.5 mb-6">
        <div className="bg-white/[0.03] border border-white/[0.08] rounded-full px-3 py-1.5 text-xs text-zinc-400">
          <strong className="text-white mr-1">{pool.live_models ?? 0}</strong> models in pool
        </div>
        <div className="bg-white/[0.03] border border-white/[0.08] rounded-full px-3 py-1.5 text-xs text-zinc-400">
          <strong className="text-white mr-1">{pool.active_providers ?? 0}</strong> active providers
        </div>
        {Object.keys(pool.models_by_provider || {}).sort().map(pid => (
          <div key={pid} className="bg-white/[0.03] border border-white/[0.08] rounded-full px-3 py-1.5 text-xs text-zinc-400">
            <strong className="text-white mr-1">{pid}</strong> {pool.models_by_provider[pid]} models
          </div>
        ))}
      </div>

      {presets.length > 0 && (
        <Card>
          <CardHeader>
            <h3 className="text-sm font-semibold">Free / Popular Endpoints</h3>
            <span className="text-xs text-zinc-400">Click to configure</span>
          </CardHeader>
          <CardBody>
            <div className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-3">
              {presets.map(p => (
                <button
                  key={p.id}
                  onClick={() => { setForm(f => ({ ...f, id: p.id, name: p.name, base_url: p.base_url })); setShowAdd(true) }}
                  className={`bg-white/[0.03] border border-white/[0.08] rounded-xl p-4 text-left transition-all hover:border-violet-500/50 hover:bg-white/[0.05] flex flex-col gap-2 min-h-[140px] ${p.already_configured ? 'opacity-80 border-emerald-500/30' : ''}`}
                >
                  <h4 className="text-sm font-semibold">{p.name}</h4>
                  <p className="text-xs text-zinc-400 leading-relaxed flex-1">{p.base_url}</p>
                  <div className="flex flex-wrap gap-1.5">
                    {p.free_tier && <Badge variant="free">Free</Badge>}
                    {p.speed_tier === 'ultra' && <Badge variant="fast">Ultra</Badge>}
                    {p.speed_tier === 'fast' && <Badge variant="fast">Fast</Badge>}
                    {p.already_configured && <Badge variant="ok">Configured</Badge>}
                  </div>
                </button>
              ))}
            </div>
          </CardBody>
        </Card>
      )}

      {showAdd && (
        <Card className="border-violet-500/20">
          <CardHeader>
            <h3 className="text-sm font-semibold">Add Endpoint</h3>
            <button className="text-zinc-400 hover:text-white text-lg" onClick={() => setShowAdd(false)}>×</button>
          </CardHeader>
          <CardBody className="flex flex-col gap-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div><label className="block text-xs text-zinc-400 mb-1.5">Provider ID</label><Input value={form.id} onChange={e => setForm(f => ({ ...f, id: e.target.value }))} placeholder="e.g. groq" /></div>
              <div><label className="block text-xs text-zinc-400 mb-1.5">Display Name</label><Input value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} placeholder="e.g. Groq" /></div>
            </div>
            <div><label className="block text-xs text-zinc-400 mb-1.5">Base URL (OpenAI-compatible /v1)</label><Input value={form.base_url} onChange={e => setForm(f => ({ ...f, base_url: e.target.value }))} placeholder="https://api.groq.com/openai/v1" /></div>
            <div><label className="block text-xs text-zinc-400 mb-1.5">API Keys (comma-separated)</label><Input value={form.api_keys} onChange={e => setForm(f => ({ ...f, api_keys: e.target.value }))} placeholder="gsk-..." /></div>
            <div className="flex gap-2 justify-end">
              <Button onClick={() => setShowAdd(false)}>Cancel</Button>
              <Button onClick={handleTest.bind(null, form.id)}>Test</Button>
              <Button variant="primary" onClick={handleAdd} disabled={saving}>{saving ? 'Saving...' : 'Save & Merge'}</Button>
            </div>
          </CardBody>
        </Card>
      )}

      <h3 className="text-sm font-semibold mb-3">Configured Providers</h3>
      <Card className="p-0">
        <table className="w-full">
          <thead>
            <tr className="text-left border-b border-white/[0.08]">
              <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px]">Provider</th>
              <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px]">Base URL</th>
              <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px]">Keys</th>
              <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px]">Models</th>
              <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px]">Status</th>
              <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px]">Actions</th>
            </tr>
          </thead>
          <tbody>
            {providers.map(p => {
              const active = p.runtime || (p.enabled && (p.key_count || 0) > 0)
              return (
                <tr key={p.id} className="border-b border-white/[0.08] last:border-0 hover:bg-white/[0.01]">
                  <td className="px-6 py-3.5 text-[13px]">
                    <strong>{p.name}</strong>
                    {p.free_tier && <Badge variant="free">Free</Badge>}
                    {(p.speed_tier === 'ultra' || p.speed_tier === 'fast') && <Badge variant="fast">{p.speed_tier === 'ultra' ? 'Ultra' : 'Fast'}</Badge>}
                    <div className="text-[11px] text-zinc-500 mt-0.5">{p.id}/...</div>
                  </td>
                  <td className="px-6 py-3.5 text-[13px] text-zinc-400 max-w-[220px] truncate" title={p.base_url}>{p.base_url}</td>
                  <td className="px-6 py-3.5 text-[13px] text-zinc-400">
                    {p.key_count ?? 0}
                    {p.available_keys != null && <span className="text-zinc-500 ml-1">({p.available_keys} ready)</span>}
                  </td>
                  <td className="px-6 py-3.5 text-[13px] text-violet-400 font-semibold">{p.model_count ?? 0}</td>
                  <td className="px-6 py-3.5">
                    <Badge variant={active ? 'ok' : !p.enabled ? 'default' : 'err'}>
                      <StatusDot ok={!!active} />
                      {!p.enabled ? 'Disabled' : active ? 'In pool' : 'No keys'}
                    </Badge>
                  </td>
                  <td className="px-6 py-3.5 flex gap-1.5 flex-wrap">
                    <Button size="sm" onClick={() => { setForm({ id: p.id, name: p.name, base_url: p.base_url, api_keys: '', rpm_limit: p.rpm_limit, rpd_limit: p.rpd_limit }); setShowAdd(true) }}>Edit</Button>
                    <Button size="sm" onClick={() => handleTest(p.id)}>Test</Button>
                    {!p.builtin && <Button size="sm" variant="danger" onClick={() => handleDelete(p.id)}>Delete</Button>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </Card>
    </div>
  )
}
