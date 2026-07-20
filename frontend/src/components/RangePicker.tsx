export function RangePicker({
  value,
  onChange,
}: {
  value: string
  onChange: (v: string) => void
}) {
  const opts = ['1h', '6h', '24h', '7d']
  return (
    <div className="inline-flex rounded-lg border border-white/[0.08] overflow-hidden">
      {opts.map(o => (
        <button
          key={o}
          type="button"
          onClick={() => onChange(o)}
          className={
            value === o
              ? 'px-3 py-1.5 text-xs bg-violet-500/20 text-violet-300'
              : 'px-3 py-1.5 text-xs text-zinc-400 hover:bg-white/[0.04]'
          }
        >
          {o}
        </button>
      ))}
    </div>
  )
}
