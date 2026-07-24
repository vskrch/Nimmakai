import { useState } from 'react'
import { Card, CardBody, CardHeader, Button, Input, Spinner, StatBox } from '../components/ui'
import { HorizontalBars } from '../components/charts'
import { useBreakdown, useCostRates, useAnalyticsSummary } from '../hooks/useAnalytics'
import { RangePicker } from '../components/RangePicker'
import { fmtUsd, fmtTokens } from '../lib/format'
import { api, errMsg, okBody } from '../lib/api'

export default function CostPage() {
  const [range, setRange] = useState('24h')
  const { data: summary, reload: reloadSummary } = useAnalyticsSummary(range)
  const { items: models } = useBreakdown('models', range)
  const { items: keys } = useBreakdown('api_keys', range)
  const { data: rates, loading: ratesLoading, error: ratesError, reload: reloadRates } = useCostRates()
  const [modelId, setModelId] = useState('')
  const [inp, setInp] = useState('0')
  const [out, setOut] = useState('0')
  const [saving, setSaving] = useState(false)
  const [importing, setImporting] = useState(false)
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null)

  async function saveOverride() {
    if (!modelId.trim()) return
    setSaving(true)
    const r = await api(`/analytics/cost/rates/${encodeURIComponent(modelId.trim())}`, {
      method: 'PUT',
      body: JSON.stringify({ input_per_m: Number(inp), output_per_m: Number(out) }),
    })
    setSaving(false)
    if (okBody(r)) {
      setMsg({ text: 'Rate saved', ok: true })
      reloadRates()
      reloadSummary()
    } else {
      setMsg({ text: errMsg(r, 'Failed to save rate'), ok: false })
    }
  }

  async function bulkImport(overwrite: boolean) {
    setImporting(true)
    const r = await api('/analytics/cost/rates/import', {
      method: 'POST',
      body: JSON.stringify({ overwrite }),
    })
    setImporting(false)
    if (okBody(r)) {
      const d = r as Record<string, unknown>
      setMsg({ text: `Imported ${d.imported} rates (${d.skipped} skipped)`, ok: true })
      reloadRates()
      reloadSummary()
    } else {
      setMsg({ text: errMsg(r, 'Import failed'), ok: false })
    }
  }

  const costItems = models
    .map(m => ({ key: String(m.key), request_count: Math.round((m.cost_usd || 0) * 10000) / 10000, _cost: m.cost_usd || 0 }))
    .sort((a, b) => b._cost - a._cost)

  return (
    <div className="animate-[fadeIn_0.3s_ease]">
      <div className="flex items-center justify-between mb-6 gap-3 flex-wrap">
        <h2 className="text-xl font-semibold">Cost Center</h2>
        <RangePicker value={range} onChange={setRange} />
      </div>

      {msg && (
        <div className={`mb-4 p-3 rounded-lg text-[13px] ${msg.ok ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-red-500/10 text-red-400 border border-red-500/20'}`}>
          {msg.text}
          <button className="ml-3 text-xs opacity-60" onClick={() => setMsg(null)}>dismiss</button>
        </div>
      )}

      <div className="grid grid-cols-[repeat(auto-fit,minmax(160px,1fr))] gap-4 mb-8">
        <StatBox label="Estimated spend" value={fmtUsd(summary?.estimated_cost_usd)} sub={range} />
        <StatBox label="Tokens" value={fmtTokens(summary?.total_tokens)} sub={`${fmtTokens(summary?.total_prompt_tokens)} in`} />
        <StatBox label="Requests" value={(summary?.total_requests ?? 0).toLocaleString()} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card>
          <CardHeader><h3 className="text-sm font-semibold">Cost by Model</h3></CardHeader>
          <CardBody>
            {!costItems.length ? <div className="text-sm text-zinc-500">No spend data</div> : (
              <div className="flex flex-col gap-2">
                {costItems.slice(0, 12).map((it, i) => (
                  <div key={it.key + i} className="flex justify-between text-[13px]">
                    <span className="text-zinc-400 truncate max-w-[60%]">{it.key.split('/').pop()}</span>
                    <span className="tabular-nums">{fmtUsd(it._cost)}</span>
                  </div>
                ))}
              </div>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader><h3 className="text-sm font-semibold">Usage by API Key</h3></CardHeader>
          <CardBody>
            <HorizontalBars
              items={keys.map(k => ({
                key: String(k.key || 'anon').slice(0, 16),
                request_count: k.request_count,
              }))}
            />
          </CardBody>
        </Card>
      </div>

      <Card>
        <CardHeader><h3 className="text-sm font-semibold">Custom Cost Rates ($/M tokens)</h3></CardHeader>
        <CardBody>
          <div className="flex gap-2 flex-wrap mb-4">
            <Input placeholder="model id" value={modelId} onChange={e => setModelId(e.target.value)} className="max-w-[220px]" />
            <Input placeholder="input $/M" value={inp} onChange={e => setInp(e.target.value)} className="max-w-[120px]" />
            <Input placeholder="output $/M" value={out} onChange={e => setOut(e.target.value)} className="max-w-[120px]" />
            <Button onClick={saveOverride} disabled={saving}>{saving ? 'Saving…' : 'Save override'}</Button>
          </div>
          <div className="flex gap-2 flex-wrap mb-4">
            <Button size="sm" onClick={() => bulkImport(false)} disabled={importing}>
              {importing ? 'Importing…' : 'Auto-fill from models.dev'}
            </Button>
            <Button size="sm" variant="danger" onClick={() => bulkImport(true)} disabled={importing}>
              {importing ? 'Importing…' : 'Overwrite all from models.dev'}
            </Button>
            <span className="text-xs text-zinc-500 self-center">Import pricing for all live models</span>
          </div>
          {ratesError && (
            <div className="mb-3 text-sm text-red-400">{ratesError}</div>
          )}
          {ratesLoading && !rates ? <Spinner /> : !rates ? (
            <div className="text-sm text-zinc-500">No rate data</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-[13px]">
                <thead>
                  <tr className="text-left border-b border-white/[0.08]">
                    <th className="px-3 py-2 text-zinc-400 text-[11px] uppercase">Model</th>
                    <th className="px-3 py-2 text-zinc-400 text-[11px] uppercase">Input</th>
                    <th className="px-3 py-2 text-zinc-400 text-[11px] uppercase">Output</th>
                    <th className="px-3 py-2 text-zinc-400 text-[11px] uppercase">Source</th>
                  </tr>
                </thead>
                <tbody>
                  {(rates.overrides || []).map(r => (
                    <tr key={`o-${r.model_id}`} className="border-b border-white/[0.05]">
                      <td className="px-3 py-2 font-mono">{r.model_id}</td>
                      <td className="px-3 py-2">${r.input_per_m}</td>
                      <td className="px-3 py-2">${r.output_per_m}</td>
                      <td className="px-3 py-2 text-violet-300">override</td>
                    </tr>
                  ))}
                  {(rates.defaults || []).slice(0, 20).map(r => (
                    <tr key={`d-${r.model_id}`} className="border-b border-white/[0.05]">
                      <td className="px-3 py-2 font-mono text-zinc-400">{r.model_id}</td>
                      <td className="px-3 py-2 text-zinc-400">${r.input_per_m}</td>
                      <td className="px-3 py-2 text-zinc-400">${r.output_per_m}</td>
                      <td className="px-3 py-2 text-zinc-500">default</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardBody>
      </Card>
    </div>
  )
}
