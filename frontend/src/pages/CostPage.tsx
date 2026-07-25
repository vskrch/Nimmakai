import React, { useState } from 'react'
import { Card, CardBody, CardHeader, Button, Input, Spinner, StatBox, Badge } from '../components/ui'
import { HorizontalBars } from '../components/charts'
import { useBreakdown, useCostRates, useAnalyticsSummary } from '../hooks/useAnalytics'
import { RangePicker } from '../components/RangePicker'
import { fmtUsd, fmtTokens } from '../lib/format'
import { api, errMsg, okBody } from '../lib/api'
import {
  Coins,
  Save,
  Download,
  Key,
  Cpu,
  DollarSign,
  CheckCircle2,
  AlertCircle
} from 'lucide-react'

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
      setMsg({ text: 'Rate override saved', ok: true })
      reloadRates()
      reloadSummary()
    } else {
      setMsg({ text: errMsg(r, 'Failed to save rate override'), ok: false })
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
      setMsg({ text: `Imported ${d.imported} cost rates (${d.skipped} skipped)`, ok: true })
      reloadRates()
      reloadSummary()
    } else {
      setMsg({ text: errMsg(r, 'Cost rate import failed'), ok: false })
    }
  }

  const costItems = models
    .map(m => ({ key: String(m.key), request_count: Math.round((m.cost_usd || 0) * 10000) / 10000, _cost: m.cost_usd || 0 }))
    .sort((a, b) => b._cost - a._cost)

  return (
    <div className="space-y-6 animate-[fadeIn_0.25s_ease-out]">
      {/* Header controls */}
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <Coins className="w-5 h-5 text-violet-400" />
          <h2 className="text-lg font-bold text-white tracking-tight">Financial & Token Cost Center</h2>
        </div>
        <RangePicker value={range} onChange={setRange} />
      </div>

      {msg && (
        <div className={`p-4 rounded-xl text-xs flex items-center justify-between font-medium ${
          msg.ok ? 'bg-emerald-500/10 text-emerald-300 border border-emerald-500/20' : 'bg-rose-500/10 text-rose-300 border border-rose-500/20'
        }`}>
          <div className="flex items-center gap-2">
            {msg.ok ? <CheckCircle2 className="w-4 h-4 text-emerald-400" /> : <AlertCircle className="w-4 h-4 text-rose-400" />}
            <span>{msg.text}</span>
          </div>
          <button className="text-zinc-400 hover:text-white" onClick={() => setMsg(null)}>Dismiss</button>
        </div>
      )}

      {/* Primary Financial Overview Stats */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <StatBox
          label="Estimated Expenditure"
          value={fmtUsd(summary?.estimated_cost_usd)}
          sub={`Cost window: ${range}`}
          icon={Coins}
        />
        <StatBox
          label="Token Volume"
          value={fmtTokens(summary?.total_tokens)}
          sub={`${fmtTokens(summary?.total_prompt_tokens)} prompt tokens`}
          icon={Cpu}
        />
        <StatBox
          label="Total API Requests"
          value={(summary?.total_requests ?? 0).toLocaleString()}
          sub="Requests processed"
          icon={DollarSign}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Cpu className="w-4 h-4 text-violet-400" />
              <h3 className="text-sm font-semibold text-white">Expenditure by Model</h3>
            </div>
          </CardHeader>
          <CardBody>
            {!costItems.length ? (
              <div className="text-xs text-zinc-500 py-8 text-center">No spend data in selected range</div>
            ) : (
              <div className="space-y-3 text-xs">
                {costItems.slice(0, 12).map((it, i) => (
                  <div key={it.key + i} className="flex justify-between items-center p-3 rounded-xl bg-zinc-950/60 border border-white/[0.06]">
                    <span className="text-zinc-300 font-mono truncate max-w-[220px]" title={it.key}>
                      {it.key.split('/').pop()}
                    </span>
                    <span className="font-mono text-violet-300 font-semibold">{fmtUsd(it._cost)}</span>
                  </div>
                ))}
              </div>
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Key className="w-4 h-4 text-violet-400" />
              <h3 className="text-sm font-semibold text-white">Usage Distribution by API Key</h3>
            </div>
          </CardHeader>
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

      {/* Model Pricing Rate Configuration */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <DollarSign className="w-4 h-4 text-violet-400" />
            <h3 className="text-sm font-semibold text-white">Model Cost Rates ($ / 1M Tokens)</h3>
          </div>
        </CardHeader>
        <CardBody className="space-y-6">
          {/* Custom Override Form */}
          <div className="space-y-2">
            <h4 className="text-xs font-semibold uppercase tracking-wider text-zinc-400">Add Rate Override</h4>
            <div className="flex gap-2 flex-wrap">
              <Input
                placeholder="Model ID (e.g. opencode/mimo-v2.5-free)"
                value={modelId}
                onChange={e => setModelId(e.target.value)}
                className="max-w-[260px]"
              />
              <Input
                placeholder="Input $/1M"
                value={inp}
                onChange={e => setInp(e.target.value)}
                className="max-w-[130px]"
              />
              <Input
                placeholder="Output $/1M"
                value={out}
                onChange={e => setOut(e.target.value)}
                className="max-w-[130px]"
              />
              <Button onClick={saveOverride} disabled={saving} variant="primary">
                <Save className="w-3.5 h-3.5" />
                <span>{saving ? 'Saving…' : 'Save Override'}</span>
              </Button>
            </div>
          </div>

          {/* Bulk Import Options */}
          <div className="p-4 rounded-xl bg-zinc-950/60 border border-white/[0.06] flex items-center justify-between gap-4 flex-wrap">
            <div>
              <h4 className="text-xs font-semibold text-white">Sync Pricing from models.dev</h4>
              <p className="text-[11px] text-zinc-400">Import community benchmark rates for input and output token pricing.</p>
            </div>
            <div className="flex gap-2">
              <Button size="sm" variant="secondary" onClick={() => bulkImport(false)} disabled={importing}>
                <Download className="w-3.5 h-3.5" />
                <span>{importing ? 'Importing…' : 'Auto-fill Missing Rates'}</span>
              </Button>
              <Button size="sm" variant="danger" onClick={() => bulkImport(true)} disabled={importing}>
                <span>{importing ? 'Importing…' : 'Overwrite All Rates'}</span>
              </Button>
            </div>
          </div>

          {ratesError && (
            <div className="text-xs text-rose-400 bg-rose-500/10 border border-rose-500/20 p-3 rounded-xl">
              {ratesError}
            </div>
          )}

          {/* Rates Table */}
          {ratesLoading && !rates ? (
            <Spinner />
          ) : !rates ? (
            <div className="text-xs text-zinc-500 py-6 text-center">No cost rate data available</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-white/[0.08] text-[10px] uppercase tracking-wider text-zinc-400 bg-white/[0.01]">
                    <th className="px-5 py-3.5 font-semibold">Model ID</th>
                    <th className="px-5 py-3.5 font-semibold">Input $/1M Tokens</th>
                    <th className="px-5 py-3.5 font-semibold">Output $/1M Tokens</th>
                    <th className="px-5 py-3.5 font-semibold">Pricing Source</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/[0.06]">
                  {(rates.overrides || []).map(r => (
                    <tr key={`o-${r.model_id}`} className="hover:bg-white/[0.02] transition-colors">
                      <td className="px-5 py-3.5 font-mono text-white font-semibold">{r.model_id}</td>
                      <td className="px-5 py-3.5 font-mono text-zinc-200">${r.input_per_m}</td>
                      <td className="px-5 py-3.5 font-mono text-zinc-200">${r.output_per_m}</td>
                      <td className="px-5 py-3.5">
                        <Badge variant="accent">custom override</Badge>
                      </td>
                    </tr>
                  ))}
                  {(rates.defaults || []).slice(0, 20).map(r => (
                    <tr key={`d-${r.model_id}`} className="hover:bg-white/[0.02] transition-colors">
                      <td className="px-5 py-3.5 font-mono text-zinc-400">{r.model_id}</td>
                      <td className="px-5 py-3.5 font-mono text-zinc-400">${r.input_per_m}</td>
                      <td className="px-5 py-3.5 font-mono text-zinc-400">${r.output_per_m}</td>
                      <td className="px-5 py-3.5">
                        <Badge variant="default">models.dev</Badge>
                      </td>
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
