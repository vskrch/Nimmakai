import React, { useState } from 'react'
import { Card, CardBody, CardHeader, StatBox, Badge } from '../components/ui'
import { Donut, HorizontalBars } from '../components/charts'
import { useBreakdown } from '../hooks/useAnalytics'
import { RangePicker } from '../components/RangePicker'
import { fmtPct, fmtMs } from '../lib/format'
import {
  BrainCircuit,
  PieChart,
  Layers,
  GitBranch,
  AlertTriangle,
  Target
} from 'lucide-react'

export default function IntentsPage() {
  const [range, setRange] = useState('24h')
  const { items: intents } = useBreakdown('intents', range)
  const { items: fallbacks } = useBreakdown('fallbacks', range)
  const { items: errors } = useBreakdown('errors', range)

  const total = intents.reduce((a, b) => a + b.request_count, 0)
  const avgConf = intents.length
    ? (
        intents.reduce((a, b) => a + (b.avg_confidence || 0) * b.request_count, 0) /
        Math.max(1, total)
      ).toFixed(2)
    : '—'

  return (
    <div className="space-y-6 animate-[fadeIn_0.25s_ease-out]">
      {/* Header controls */}
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <BrainCircuit className="w-5 h-5 text-violet-400" />
          <h2 className="text-lg font-bold text-white tracking-tight">Intent Classification Telemetry</h2>
        </div>
        <RangePicker value={range} onChange={setRange} />
      </div>

      {/* Overview Stats */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <StatBox
          label="Total Classified Prompts"
          value={total.toLocaleString()}
          sub="Requests routed via classifier"
          icon={BrainCircuit}
        />
        <StatBox
          label="Unique Intent Types"
          value={intents.length}
          sub="Active category classification"
          icon={Layers}
        />
        <StatBox
          label="Average Confidence"
          value={avgConf}
          sub="Classifier score probability"
          color="text-emerald-400"
          icon={Target}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <PieChart className="w-4 h-4 text-violet-400" />
              <h3 className="text-sm font-semibold text-white">Intent Classification Flow</h3>
            </div>
          </CardHeader>
          <CardBody>
            {!intents.length ? (
              <div className="text-xs text-zinc-500 py-8 text-center">No intent metrics for this timeframe</div>
            ) : (
              <Donut items={intents.map(i => ({ key: String(i.key || 'unknown'), value: i.request_count }))} />
            )}
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Layers className="w-4 h-4 text-violet-400" />
              <h3 className="text-sm font-semibold text-white">Intent & Rule Telemetry Detail</h3>
            </div>
          </CardHeader>
          <CardBody className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-white/[0.08] text-[10px] uppercase tracking-wider text-zinc-400 bg-white/[0.01]">
                    <th className="px-5 py-3.5 font-semibold">Intent</th>
                    <th className="px-5 py-3.5 font-semibold">Requests</th>
                    <th className="px-5 py-3.5 font-semibold">Avg Conf</th>
                    <th className="px-5 py-3.5 font-semibold">Err Rate</th>
                    <th className="px-5 py-3.5 font-semibold">Avg Latency</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/[0.06]">
                  {intents.map(i => (
                    <tr key={String(i.key)} className="hover:bg-white/[0.02] transition-colors">
                      <td className="px-5 py-3.5 font-semibold text-white">{i.key || '—'}</td>
                      <td className="px-5 py-3.5 font-mono text-zinc-300">{i.request_count.toLocaleString()}</td>
                      <td className="px-5 py-3.5 font-mono text-emerald-400">{(i.avg_confidence ?? 0).toFixed(2)}</td>
                      <td className="px-5 py-3.5 font-mono text-rose-400">{fmtPct(i.error_rate)}</td>
                      <td className="px-5 py-3.5 font-mono text-zinc-200">{fmtMs(i.avg_latency_ms)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <GitBranch className="w-4 h-4 text-violet-400" />
              <h3 className="text-sm font-semibold text-white">Fallback Chain Index Distribution</h3>
            </div>
          </CardHeader>
          <CardBody>
            <HorizontalBars
              items={fallbacks.map(f => ({
                key: `Chain Index [${f.key}]`,
                request_count: f.request_count,
              }))}
            />
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <AlertTriangle className="w-4 h-4 text-rose-400" />
              <h3 className="text-sm font-semibold text-white">Top Frequency Error Traces</h3>
            </div>
          </CardHeader>
          <CardBody>
            {!errors.length ? (
              <div className="text-xs text-zinc-500 py-8 text-center">No error logs reported</div>
            ) : (
              <div className="space-y-3 text-xs">
                {errors.slice(0, 10).map((e, i) => (
                  <div key={i} className="flex items-center justify-between gap-3 p-3 rounded-xl bg-zinc-950/60 border border-white/[0.06]">
                    <span className="text-zinc-300 font-mono truncate max-w-[280px]" title={String(e.key)}>
                      {String(e.key)}
                    </span>
                    <Badge variant="err">{e.request_count} failures</Badge>
                  </div>
                ))}
              </div>
            )}
          </CardBody>
        </Card>
      </div>
    </div>
  )
}
