import React from 'react'
import { clsx } from 'clsx'
import { BarChart2, PieChart } from 'lucide-react'

/** SVG Sparkline with SVG gradient fill */
export function Sparkline({
  values,
  width = 140,
  height = 36,
  className,
  stroke = '#a78bfa',
  fill = 'url(#sparkline-grad)',
}: {
  values: number[]
  width?: number
  height?: number
  className?: string
  stroke?: string
  fill?: string
}) {
  if (!values.length) {
    return <svg width={width} height={height} className={className} />
  }
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const pts = values.map((v, i) => {
    const x = (i / Math.max(1, values.length - 1)) * width
    const y = height - ((v - min) / span) * (height - 6) - 3
    return `${x},${y}`
  })
  const line = pts.join(' ')
  const area = `0,${height} ${line} ${width},${height}`

  return (
    <svg width={width} height={height} className={className} viewBox={`0 0 ${width} ${height}`}>
      <defs>
        <linearGradient id="sparkline-grad" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="#a78bfa" stopOpacity="0.35" />
          <stop offset="100%" stopColor="#a78bfa" stopOpacity="0.0" />
        </linearGradient>
      </defs>
      <polygon points={area} fill={fill} />
      <polyline points={line} fill="none" stroke={stroke} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}

export function StackedBars({
  points,
  height = 180,
  className,
}: {
  points: { ts: number; success?: number; errors?: number; requests?: number }[]
  height?: number
  className?: string
}) {
  if (!points.length) {
    return (
      <div className={clsx('flex flex-col items-center justify-center text-zinc-500 text-xs gap-2', className)} style={{ height }}>
        <BarChart2 className="w-6 h-6 stroke-1 text-zinc-600" />
        <span>No request metrics recorded in this window</span>
      </div>
    )
  }
  const max = Math.max(...points.map(p => (p.requests ?? ((p.success || 0) + (p.errors || 0)))), 1)

  return (
    <div className={clsx('flex items-end gap-1 pt-4 pb-1', className)} style={{ height }}>
      {points.map((p, i) => {
        const ok = p.success ?? 0
        const err = p.errors ?? Math.max(0, (p.requests || 0) - ok)
        const total = ok + err || p.requests || 0
        const h = (total / max) * (height - 24)
        const errH = total ? (err / total) * h : 0
        const okH = h - errH

        return (
          <div
            key={p.ts + i}
            className="flex-1 min-w-[3px] flex flex-col justify-end group relative cursor-pointer"
          >
            {/* Tooltip on hover */}
            <div className="absolute bottom-full mb-2 left-1/2 -translate-x-1/2 hidden group-hover:flex flex-col gap-1 bg-zinc-900 border border-white/10 rounded-lg p-2 text-[10px] text-zinc-200 whitespace-nowrap z-30 shadow-xl pointer-events-none">
              <span className="font-semibold text-zinc-400">{new Date(p.ts * 1000).toLocaleTimeString()}</span>
              <span className="text-emerald-400">{ok} Successful</span>
              {err > 0 && <span className="text-rose-400">{err} Errors</span>}
            </div>

            <div className="w-full rounded-t-sm bg-rose-500/80 group-hover:bg-rose-400 transition-colors" style={{ height: errH }} />
            <div className="w-full rounded-t-sm bg-gradient-to-t from-violet-600/70 to-violet-400/80 group-hover:from-violet-500 group-hover:to-violet-300 transition-colors" style={{ height: okH }} />
          </div>
        )
      })}
    </div>
  )
}

export function HorizontalBars({
  items,
  valueKey = 'request_count',
  labelKey = 'key',
  className,
}: {
  items: Record<string, unknown>[]
  valueKey?: string
  labelKey?: string
  className?: string
}) {
  if (!items.length) {
    return (
      <div className="text-xs text-zinc-500 py-8 flex flex-col items-center gap-2">
        <BarChart2 className="w-6 h-6 stroke-1 text-zinc-600" />
        <span>No distribution data</span>
      </div>
    )
  }
  const max = Math.max(...items.map(i => Number(i[valueKey] || 0)), 1)

  return (
    <div className={clsx('flex flex-col gap-3', className)}>
      {items.slice(0, 10).map((item, idx) => {
        const label = String(item[labelKey] || '—')
        const val = Number(item[valueKey] || 0)
        const pct = Math.round((val / max) * 100)

        return (
          <div key={label + idx} className="flex flex-col gap-1 text-[12px]">
            <div className="flex justify-between items-center text-xs">
              <span className="truncate text-zinc-300 font-medium max-w-[200px]" title={label}>
                {label.split('/').pop()}
              </span>
              <span className="tabular-nums text-zinc-400 font-mono text-[11px]">{val.toLocaleString()} reqs</span>
            </div>
            <div className="w-full h-2 bg-zinc-950 rounded-full overflow-hidden border border-white/[0.05]">
              <div
                className="h-full bg-gradient-to-r from-violet-600 to-fuchsia-500 rounded-full transition-all duration-500"
                style={{ width: `${pct}%` }}
              />
            </div>
          </div>
        )
      })}
    </div>
  )
}

export function Donut({
  items,
  size = 140,
}: {
  items: { key: string; value: number; color?: string }[]
  size?: number
}) {
  const filtered = items.filter(i => i.value > 0)
  if (!filtered.length) {
    return (
      <div className="text-xs text-zinc-500 py-8 flex flex-col items-center gap-2">
        <PieChart className="w-6 h-6 stroke-1 text-zinc-600" />
        <span>No categorical metrics</span>
      </div>
    )
  }
  const total = filtered.reduce((a, b) => a + b.value, 0)
  const colors = ['#8b5cf6', '#06b6d4', '#10b981', '#f59e0b', '#ec4899', '#3b82f6', '#84cc16']
  let acc = 0
  const r = size / 2 - 10
  const c = size / 2
  const circ = 2 * Math.PI * r

  return (
    <div className="flex items-center gap-6">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="shrink-0">
        {filtered.map((it, i) => {
          const frac = it.value / total
          const dash = circ * frac
          const offset = circ * (1 - acc) + circ * 0.25
          acc += frac
          return (
            <circle
              key={it.key}
              cx={c}
              cy={c}
              r={r}
              fill="none"
              stroke={it.color || colors[i % colors.length]}
              strokeWidth="14"
              strokeDasharray={`${dash} ${circ - dash}`}
              strokeDashoffset={offset}
              className="transition-all duration-300 hover:opacity-80"
            />
          )
        })}
        <text x={c} y={c - 4} textAnchor="middle" dominantBaseline="middle" className="fill-white text-base font-bold font-mono">
          {total.toLocaleString()}
        </text>
        <text x={c} y={c + 14} textAnchor="middle" dominantBaseline="middle" className="fill-zinc-500 text-[10px] uppercase font-semibold">
          Total
        </text>
      </svg>
      <div className="flex flex-col gap-2 text-[12px] min-w-0">
        {filtered.slice(0, 6).map((it, i) => (
          <div key={it.key} className="flex items-center gap-2.5 min-w-0">
            <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: it.color || colors[i % colors.length] }} />
            <span className="text-zinc-300 truncate max-w-[120px] font-medium">{it.key}</span>
            <span className="text-zinc-500 font-mono text-[11px] ml-auto shrink-0">{Math.round((it.value / total) * 100)}%</span>
          </div>
        ))}
      </div>
    </div>
  )
}
