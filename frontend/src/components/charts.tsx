import { clsx } from 'clsx'

/** Minimal SVG sparkline / area chart — no heavy chart library. */
export function Sparkline({
  values,
  width = 120,
  height = 32,
  className,
  stroke = '#a78bfa',
  fill = 'rgba(167,139,250,0.15)',
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
    const y = height - ((v - min) / span) * (height - 4) - 2
    return `${x},${y}`
  })
  const line = pts.join(' ')
  const area = `0,${height} ${line} ${width},${height}`
  return (
    <svg width={width} height={height} className={className} viewBox={`0 0 ${width} ${height}`}>
      <polygon points={area} fill={fill} />
      <polyline points={line} fill="none" stroke={stroke} strokeWidth="1.5" strokeLinejoin="round" />
    </svg>
  )
}

export function StackedBars({
  points,
  height = 160,
  className,
}: {
  points: { ts: number; success?: number; errors?: number; requests?: number }[]
  height?: number
  className?: string
}) {
  if (!points.length) {
    return (
      <div className={clsx('flex items-center justify-center text-zinc-500 text-sm', className)} style={{ height }}>
        No data in range
      </div>
    )
  }
  const max = Math.max(...points.map(p => (p.requests ?? ((p.success || 0) + (p.errors || 0)))), 1)
  return (
    <div className={clsx('flex items-end gap-0.5', className)} style={{ height }}>
      {points.map((p, i) => {
        const ok = p.success ?? 0
        const err = p.errors ?? Math.max(0, (p.requests || 0) - ok)
        const total = ok + err || p.requests || 0
        const h = (total / max) * (height - 8)
        const errH = total ? (err / total) * h : 0
        const okH = h - errH
        return (
          <div
            key={p.ts + i}
            className="flex-1 min-w-[2px] flex flex-col justify-end group relative"
            title={`${new Date(p.ts * 1000).toLocaleString()}: ${total} req (${err} err)`}
          >
            <div className="w-full rounded-t-sm bg-red-500/70" style={{ height: errH }} />
            <div className="w-full rounded-t-sm bg-violet-500/60" style={{ height: okH }} />
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
    return <div className="text-sm text-zinc-500 py-4">No data</div>
  }
  const max = Math.max(...items.map(i => Number(i[valueKey] || 0)), 1)
  return (
    <div className={clsx('flex flex-col gap-2', className)}>
      {items.slice(0, 10).map((item, idx) => {
        const label = String(item[labelKey] || '—')
        const val = Number(item[valueKey] || 0)
        return (
          <div key={label + idx} className="flex items-center gap-3 text-[13px]">
            <span className="w-36 truncate text-zinc-400 shrink-0" title={label}>
              {label.split('/').pop()}
            </span>
            <div className="flex-1 h-2 bg-white/[0.04] rounded-full overflow-hidden">
              <div
                className="h-full bg-gradient-to-r from-violet-500/80 to-fuchsia-500/60 rounded-full"
                style={{ width: `${(val / max) * 100}%` }}
              />
            </div>
            <span className="w-14 text-right tabular-nums text-zinc-300">{val.toLocaleString()}</span>
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
  const total = items.reduce((a, b) => a + b.value, 0) || 1
  const colors = ['#8b5cf6', '#22d3ee', '#34d399', '#fbbf24', '#f472b6', '#60a5fa', '#a3e635']
  let acc = 0
  const r = size / 2 - 8
  const c = size / 2
  const circ = 2 * Math.PI * r
  return (
    <div className="flex items-center gap-6">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        {items.map((it, i) => {
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
              strokeWidth="16"
              strokeDasharray={`${dash} ${circ - dash}`}
              strokeDashoffset={offset}
              className="transition-all"
            />
          )
        })}
        <text x={c} y={c} textAnchor="middle" dominantBaseline="middle" className="fill-white text-sm font-semibold" fontSize="14">
          {total.toLocaleString()}
        </text>
      </svg>
      <div className="flex flex-col gap-1.5 text-[12px]">
        {items.slice(0, 6).map((it, i) => (
          <div key={it.key} className="flex items-center gap-2">
            <span className="w-2 h-2 rounded-full" style={{ background: it.color || colors[i % colors.length] }} />
            <span className="text-zinc-400 truncate max-w-[140px]">{it.key}</span>
            <span className="text-zinc-300 tabular-nums">{Math.round((it.value / total) * 100)}%</span>
          </div>
        ))}
      </div>
    </div>
  )
}
