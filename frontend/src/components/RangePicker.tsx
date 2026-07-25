import React from 'react'
import { clsx } from 'clsx'

export function RangePicker({
  value,
  onChange,
}: {
  value: string
  onChange: (v: string) => void
}) {
  const opts = ['1h', '6h', '24h', '7d']
  return (
    <div className="inline-flex rounded-xl p-1 bg-zinc-900 border border-white/[0.08]">
      {opts.map(o => (
        <button
          key={o}
          type="button"
          onClick={() => onChange(o)}
          className={clsx(
            'px-3 py-1 text-xs font-semibold rounded-lg transition-all cursor-pointer',
            value === o
              ? 'bg-violet-500/20 text-violet-200 border border-violet-500/30 shadow-[0_0_10px_rgba(139,92,246,0.2)]'
              : 'text-zinc-400 hover:text-zinc-200 hover:bg-white/[0.04]'
          )}
        >
          {o}
        </button>
      ))}
    </div>
  )
}
