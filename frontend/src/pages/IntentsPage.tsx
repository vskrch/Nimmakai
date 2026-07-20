import { useState } from 'react'
import { Card, CardBody, CardHeader, StatBox } from '../components/ui'
import { Donut, HorizontalBars } from '../components/charts'
import { useBreakdown } from '../hooks/useAnalytics'
import { RangePicker } from '../components/RangePicker'
import { fmtPct, fmtMs } from '../lib/format'

export default function IntentsPage() {
  const [range, setRange] = useState('24h')
  const { items: intents } = useBreakdown('intents', range)
  const { items: fallbacks } = useBreakdown('fallbacks', range)
  const { items: errors } = useBreakdown('errors', range)

  const total = intents.reduce((a, b) => a + b.request_count, 0)

  return (
    <div className="animate-[fadeIn_0.3s_ease]">
      <div className="flex items-center justify-between mb-6 gap-3 flex-wrap">
        <h2 className="text-xl font-semibold">Intent Analytics</h2>
        <RangePicker value={range} onChange={setRange} />
      </div>

      <div className="grid grid-cols-[repeat(auto-fit,minmax(160px,1fr))] gap-4 mb-8">
        <StatBox label="Total classified" value={total.toLocaleString()} />
        <StatBox label="Intent types" value={intents.length} />
        <StatBox
          label="Avg confidence"
          value={
            intents.length
              ? (
                  intents.reduce((a, b) => a + (b.avg_confidence || 0) * b.request_count, 0) /
                  Math.max(1, total)
                ).toFixed(2)
              : '—'
          }
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <Card>
          <CardHeader><h3 className="text-sm font-semibold">Intent flow</h3></CardHeader>
          <CardBody>
            {!intents.length ? <div className="text-sm text-zinc-500">No data</div> : (
              <Donut items={intents.map(i => ({ key: String(i.key || 'unknown'), value: i.request_count }))} />
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader><h3 className="text-sm font-semibold">Rule / intent detail</h3></CardHeader>
          <CardBody className="p-0">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="text-left border-b border-white/[0.08]">
                  <th className="px-4 py-2 text-[11px] text-zinc-400 uppercase">Intent</th>
                  <th className="px-4 py-2 text-[11px] text-zinc-400 uppercase">Reqs</th>
                  <th className="px-4 py-2 text-[11px] text-zinc-400 uppercase">Conf</th>
                  <th className="px-4 py-2 text-[11px] text-zinc-400 uppercase">Err%</th>
                  <th className="px-4 py-2 text-[11px] text-zinc-400 uppercase">Lat</th>
                </tr>
              </thead>
              <tbody>
                {intents.map(i => (
                  <tr key={String(i.key)} className="border-b border-white/[0.05]">
                    <td className="px-4 py-2">{i.key || '—'}</td>
                    <td className="px-4 py-2 tabular-nums">{i.request_count}</td>
                    <td className="px-4 py-2 tabular-nums">{(i.avg_confidence ?? 0).toFixed(2)}</td>
                    <td className="px-4 py-2 tabular-nums">{fmtPct(i.error_rate)}</td>
                    <td className="px-4 py-2 tabular-nums">{fmtMs(i.avg_latency_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardBody>
        </Card>
        <Card>
          <CardHeader><h3 className="text-sm font-semibold">Fallback index distribution</h3></CardHeader>
          <CardBody>
            <HorizontalBars
              items={fallbacks.map(f => ({
                key: `chain[${f.key}]`,
                request_count: f.request_count,
              }))}
            />
          </CardBody>
        </Card>
        <Card>
          <CardHeader><h3 className="text-sm font-semibold">Top errors</h3></CardHeader>
          <CardBody>
            {!errors.length ? <div className="text-sm text-zinc-500">No errors</div> : (
              <div className="flex flex-col gap-2 text-[13px]">
                {errors.slice(0, 10).map((e, i) => (
                  <div key={i} className="flex justify-between gap-3">
                    <span className="text-zinc-400 truncate">{String(e.key).slice(0, 60)}</span>
                    <span className="tabular-nums text-red-400 shrink-0">{e.request_count}</span>
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
