import React, { useState } from 'react'
import { Card, CardBody, CardHeader, StatBox, Button, Spinner } from '../components/ui'
import { StackedBars, HorizontalBars, Donut, Sparkline } from '../components/charts'
import { useAnalyticsSummary, useTimeseries, useBreakdown } from '../hooks/useAnalytics'
import { RangePicker } from '../components/RangePicker'
import { fmtMs, fmtTokens, fmtUsd, fmtPct } from '../lib/format'
import {
  BarChart3,
  RefreshCw,
  Clock,
  Zap,
  Coins,
  CheckCircle2,
  TrendingUp,
  Cpu,
  Server,
  PieChart
} from 'lucide-react'

export default function AnalyticsOverviewPage() {
  const [range, setRange] = useState('1h')
  const { data: summary, loading, error, reload: reloadSummary } = useAnalyticsSummary(range)
  const interval = range === '7d' ? '1h' : '5m'
  const { points, reload: reloadTs } = useTimeseries('requests', range, interval)
  const { items: models, reload: reloadModels } = useBreakdown('models', range)
  const { items: intents, reload: reloadIntents } = useBreakdown('intents', range)
  const { items: providers, reload: reloadProviders } = useBreakdown('providers', range)

  function reloadAll() {
    reloadSummary()
    reloadTs()
    reloadModels()
    reloadIntents()
    reloadProviders()
  }

  if (loading && !summary) return <Spinner />

  const spark = points.map(p => p.requests || 0)

  return (
    <div className="space-y-6 animate-[fadeIn_0.25s_ease-out]">
      {/* Header controls */}
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          <BarChart3 className="w-5 h-5 text-violet-400" />
          <h2 className="text-lg font-bold text-white tracking-tight">Analytics Telemetry</h2>
        </div>
        <div className="flex items-center gap-3">
          <RangePicker value={range} onChange={setRange} />
          <Button size="sm" variant="secondary" onClick={reloadAll}>
            <RefreshCw className="w-3.5 h-3.5" />
            <span>Refresh</span>
          </Button>
        </div>
      </div>

      {error && (
        <div className="text-xs text-rose-300 bg-rose-500/10 border border-rose-500/20 rounded-xl px-4 py-3">
          {error}
        </div>
      )}

      {loading && summary && (
        <div className="text-xs text-violet-400 animate-pulse font-medium">Fetching real-time updates…</div>
      )}

      {/* Analytics Summary Stats */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
        <StatBox
          label="Total Volume"
          value={(summary?.total_requests ?? 0).toLocaleString()}
          sub={`${(summary?.requests_per_minute ?? 0).toFixed(1)} req/min`}
          icon={Zap}
        />
        <StatBox
          label="Avg Latency"
          value={fmtMs(summary?.avg_latency_ms)}
          sub={`P95: ${fmtMs(summary?.p95_latency_ms)}`}
          icon={Clock}
        />
        <StatBox
          label="Token Throughput"
          value={fmtTokens(summary?.total_tokens)}
          sub={`${fmtTokens(summary?.total_prompt_tokens)} in / ${fmtTokens(summary?.total_completion_tokens)} out`}
          icon={Cpu}
        />
        <StatBox
          label="Est. Cost"
          value={fmtUsd(summary?.estimated_cost_usd)}
          sub={`Err: ${fmtPct(summary?.error_rate)} · Fallback: ${fmtPct(summary?.fallback_rate)}`}
          icon={Coins}
        />
        <StatBox
          label="Success Rate"
          value={fmtPct(summary?.success_rate)}
          sub={`${summary?.unique_models ?? 0} models · ${summary?.active_providers ?? 0} providers`}
          color="text-emerald-400"
          icon={CheckCircle2}
        />
      </div>

      {/* Timeseries Area Chart Card */}
      <Card>
        <CardHeader>
          <div className="flex items-center gap-2">
            <TrendingUp className="w-4 h-4 text-violet-400" />
            <h3 className="text-sm font-semibold text-white">Request Throughput History</h3>
          </div>
          <Sparkline values={spark} width={180} height={32} />
        </CardHeader>
        <CardBody>
          <StackedBars points={points} height={200} />
        </CardBody>
      </Card>

      {/* Distribution Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <PieChart className="w-4 h-4 text-violet-400" />
              <h3 className="text-sm font-semibold text-white">Intent Breakdown</h3>
            </div>
          </CardHeader>
          <CardBody>
            <Donut
              items={intents.map(i => ({ key: String(i.key || 'unknown'), value: i.request_count }))}
            />
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Cpu className="w-4 h-4 text-violet-400" />
              <h3 className="text-sm font-semibold text-white">Top Models by Request Count</h3>
            </div>
          </CardHeader>
          <CardBody>
            <HorizontalBars items={models as unknown as Record<string, unknown>[]} />
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Server className="w-4 h-4 text-violet-400" />
              <h3 className="text-sm font-semibold text-white">Provider Usage Distribution</h3>
            </div>
          </CardHeader>
          <CardBody>
            <HorizontalBars items={providers as unknown as Record<string, unknown>[]} />
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Zap className="w-4 h-4 text-violet-400" />
              <h3 className="text-sm font-semibold text-white">Performance Insights</h3>
            </div>
          </CardHeader>
          <CardBody className="text-xs text-zinc-300 space-y-3 font-medium">
            <div className="flex justify-between items-center p-3 rounded-xl bg-zinc-950/60 border border-white/[0.06]">
              <span className="text-zinc-400">Most Used Model</span>
              <strong className="text-violet-300 font-semibold">{summary?.top_model || '—'}</strong>
            </div>
            <div className="flex justify-between items-center p-3 rounded-xl bg-zinc-950/60 border border-white/[0.06]">
              <span className="text-zinc-400">Primary Intent Task</span>
              <strong className="text-emerald-400 font-semibold">{summary?.top_intent || '—'}</strong>
            </div>
            <div className="flex justify-between items-center p-3 rounded-xl bg-zinc-950/60 border border-white/[0.06]">
              <span className="text-zinc-400">Avg Time-To-First-Token (TTFT)</span>
              <strong className="text-white font-semibold">{fmtMs(summary?.avg_ttft_ms)}</strong>
            </div>
          </CardBody>
        </Card>
      </div>
    </div>
  )
}
