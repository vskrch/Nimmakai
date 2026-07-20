import { useState } from 'react'
import { Card, CardBody, CardHeader, StatBox, Button, Spinner } from '../components/ui'
import { StackedBars, HorizontalBars, Donut, Sparkline } from '../components/charts'
import { useAnalyticsSummary, useTimeseries, useBreakdown } from '../hooks/useAnalytics'
import { RangePicker } from '../components/RangePicker'
import { fmtMs, fmtTokens, fmtUsd, fmtPct } from '../lib/format'

export default function AnalyticsOverviewPage() {
  const [range, setRange] = useState('1h')
  const { data: summary, loading, reload } = useAnalyticsSummary(range)
  const { points } = useTimeseries('requests', range, range === '7d' ? '1h' : '5m')
  const { items: models } = useBreakdown('models', range)
  const { items: intents } = useBreakdown('intents', range)
  const { items: providers } = useBreakdown('providers', range)

  if (loading && !summary) return <Spinner />

  const spark = points.map(p => p.requests || 0)

  return (
    <div className="animate-[fadeIn_0.3s_ease]">
      <div className="flex items-center justify-between mb-6 gap-3 flex-wrap">
        <h2 className="text-xl font-semibold">Analytics Overview</h2>
        <div className="flex items-center gap-3">
          <RangePicker value={range} onChange={setRange} />
          <Button size="sm" onClick={reload}>Refresh</Button>
        </div>
      </div>

      <div className="grid grid-cols-[repeat(auto-fit,minmax(180px,1fr))] gap-4 mb-8">
        <StatBox
          label="Requests"
          value={(summary?.total_requests ?? 0).toLocaleString()}
          sub={`${(summary?.requests_per_minute ?? 0).toFixed(1)} rpm`}
        />
        <StatBox
          label="Latency"
          value={fmtMs(summary?.avg_latency_ms)}
          sub={`p95 ${fmtMs(summary?.p95_latency_ms)}`}
        />
        <StatBox
          label="Tokens"
          value={fmtTokens(summary?.total_tokens)}
          sub={`${fmtTokens(summary?.total_prompt_tokens)} in / ${fmtTokens(summary?.total_completion_tokens)} out`}
        />
        <StatBox
          label="Cost"
          value={fmtUsd(summary?.estimated_cost_usd)}
          sub={`err ${fmtPct(summary?.error_rate)} · fb ${fmtPct(summary?.fallback_rate)}`}
        />
        <StatBox
          label="Success"
          value={fmtPct(summary?.success_rate)}
          sub={`${summary?.unique_models ?? 0} models · ${summary?.active_providers ?? 0} providers`}
          color="text-emerald-400"
        />
      </div>

      <Card>
        <CardHeader>
          <h3 className="text-sm font-semibold">Request Volume</h3>
          <Sparkline values={spark} width={160} height={28} />
        </CardHeader>
        <CardBody>
          <StackedBars points={points} height={180} />
        </CardBody>
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card>
          <CardHeader><h3 className="text-sm font-semibold">Intent Distribution</h3></CardHeader>
          <CardBody>
            <Donut
              items={intents.map(i => ({ key: String(i.key || 'unknown'), value: i.request_count }))}
            />
          </CardBody>
        </Card>
        <Card>
          <CardHeader><h3 className="text-sm font-semibold">Top Models</h3></CardHeader>
          <CardBody>
            <HorizontalBars items={models as unknown as Record<string, unknown>[]} />
          </CardBody>
        </Card>
        <Card>
          <CardHeader><h3 className="text-sm font-semibold">Providers</h3></CardHeader>
          <CardBody>
            <HorizontalBars items={providers as unknown as Record<string, unknown>[]} />
          </CardBody>
        </Card>
        <Card>
          <CardHeader><h3 className="text-sm font-semibold">Highlights</h3></CardHeader>
          <CardBody className="text-[13px] text-zinc-400 flex flex-col gap-2">
            <div>Top model: <strong className="text-white">{summary?.top_model || '—'}</strong></div>
            <div>Top intent: <strong className="text-white">{summary?.top_intent || '—'}</strong></div>
            <div>Avg TTFT: <strong className="text-white">{fmtMs(summary?.avg_ttft_ms)}</strong></div>
          </CardBody>
        </Card>
      </div>
    </div>
  )
}
