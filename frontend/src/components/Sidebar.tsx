import { clsx } from 'clsx'

interface SidebarProps {
  page: string
  onNavigate: (page: string) => void
}

const NAV_ITEMS = [
  { id: 'dashboard', label: 'Dashboard', icon: 'M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z' },
  { id: 'playground', label: 'Playground', icon: 'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z' },
  { id: 'tracing', label: 'Tracing', icon: 'M18 20V10M12 20V4M6 20v-6' },
  { id: 'providers', label: 'Providers', icon: 'M2 12h4l2-9 4 18 2-9h4' },
  { id: 'health', label: 'Health', icon: 'M22 12h-4l-3 9L9 3l-3 9H2' },
  { id: 'models', label: 'Models', icon: 'M12 2 2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5' },
  { id: 'routing', label: 'Routing', icon: 'M12 5a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM12 22V8M5 12h14' },
]

export default function Sidebar({ page, onNavigate }: SidebarProps) {
  return (
    <div className="w-60 bg-[#050505] border-r border-white/[0.08] flex flex-col z-10 shrink-0">
      <div className="px-5 py-6 flex items-center gap-3">
        <div className="w-8 h-8 bg-gradient-to-br from-violet-500 to-fuchsia-500 rounded-lg shadow-[0_0_16px_rgba(139,92,246,0.5)]" />
        <div>
          <h1 className="text-lg font-bold tracking-tight">Nimmakai</h1>
          <p className="text-[11px] text-zinc-400 uppercase tracking-[1px] mt-0.5">Gateway</p>
        </div>
      </div>
      <nav className="flex-1 px-3 flex flex-col gap-1">
        {NAV_ITEMS.map(item => (
          <button
            key={item.id}
            onClick={() => onNavigate(item.id)}
            className={clsx(
              'flex items-center gap-3 px-3.5 py-2.5 rounded-lg text-[13px] font-medium transition-all text-left',
              page === item.id
                ? 'bg-violet-500/10 text-violet-300 border border-violet-500/20 shadow-[inset_0_0_12px_rgba(139,92,246,0.05)]'
                : 'text-zinc-400 hover:bg-white/[0.03] hover:text-white border border-transparent'
            )}
          >
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="w-4 h-4 opacity-80">
              <path d={item.icon} />
            </svg>
            {item.label}
          </button>
        ))}
      </nav>
    </div>
  )
}
