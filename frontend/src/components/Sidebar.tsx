import React from 'react'
import { clsx } from 'clsx'
import {
  LayoutDashboard,
  BarChart3,
  ListFilter,
  Radio,
  BrainCircuit,
  Coins,
  Terminal,
  MessageSquare,
  User,
  Users,
  Server,
  Activity,
  Cpu,
  GitFork,
  LogOut,
  Zap,
  ShieldCheck,
  Key,
  Layers
} from 'lucide-react'

interface SidebarProps {
  page: string
  onNavigate: (page: string) => void
  isAdmin?: boolean
  email?: string | null
  onLogout?: () => void
}

interface NavItem {
  id: string
  label: string
  icon: React.ComponentType<{ className?: string }>
}

const ANALYTICS_NAV: NavItem[] = [
  { id: 'dashboard', label: 'Overview', icon: LayoutDashboard },
  { id: 'analytics', label: 'Analytics', icon: BarChart3 },
  { id: 'requests', label: 'Requests', icon: ListFilter },
  { id: 'live', label: 'Live Stream', icon: Radio },
  { id: 'intents', label: 'Intents', icon: BrainCircuit },
  { id: 'cost', label: 'Cost Center', icon: Coins },
]

const DEV_NAV: NavItem[] = [
  { id: 'chat', label: 'Chat', icon: MessageSquare },
  { id: 'playground', label: 'Playground', icon: Terminal },
  { id: 'account', label: 'API Keys & Account', icon: Key },
]

const ADMIN_NAV: NavItem[] = [
  { id: 'users', label: 'Users', icon: Users },
  { id: 'providers', label: 'LLM Providers', icon: Server },
  { id: 'health', label: 'Provider Health', icon: Activity },
  { id: 'models', label: 'Model Catalog', icon: Cpu },
  { id: 'routing', label: 'Routing Engine', icon: GitFork },
  { id: 'ladders', label: 'Model Ladders', icon: Layers },
  { id: 'rl', label: 'Adaptive RL', icon: Zap },
]

export default function Sidebar({ page, onNavigate, isAdmin, email, onLogout }: SidebarProps) {
  const renderNavGroup = (title: string, items: NavItem[]) => (
    <div className="mb-6">
      <div className="px-3 mb-2 text-[10px] font-semibold uppercase tracking-wider text-zinc-500">
        {title}
      </div>
      <div className="space-y-0.5">
        {items.map(item => {
          const Icon = item.icon
          const isActive = page === item.id
          return (
            <button
              key={item.id}
              onClick={() => onNavigate(item.id)}
              className={clsx(
                'w-full flex items-center gap-3 px-3 py-2 rounded-xl text-[13px] font-medium transition-all duration-150 text-left group',
                isActive
                  ? 'bg-violet-500/15 text-violet-200 border border-violet-500/30 shadow-[0_0_15px_rgba(139,92,246,0.15)] font-semibold'
                  : 'text-zinc-400 hover:bg-white/[0.04] hover:text-zinc-200 border border-transparent'
              )}
            >
              <Icon className={clsx('w-4 h-4 transition-colors shrink-0', isActive ? 'text-violet-400' : 'text-zinc-500 group-hover:text-zinc-300')} />
              <span className="truncate">{item.label}</span>
            </button>
          )
        })}
      </div>
    </div>
  )

  return (
    <aside className="w-64 bg-zinc-950/90 border-r border-white/[0.08] flex flex-col z-20 shrink-0 select-none">
      {/* Brand Header */}
      <div className="px-6 py-5 flex items-center gap-3 border-b border-white/[0.06]">
        <div className="w-9 h-9 bg-gradient-to-br from-violet-600 to-fuchsia-600 rounded-xl flex items-center justify-center shadow-[0_0_20px_rgba(139,92,246,0.5)] border border-white/20">
          <Zap className="w-5 h-5 text-white fill-white" />
        </div>
        <div>
          <div className="flex items-center gap-1.5">
            <h1 className="text-base font-bold tracking-tight text-white">Potato Gateway</h1>
            <span className="text-[10px] font-semibold px-1.5 py-0.2 bg-violet-500/20 text-violet-300 rounded border border-violet-500/30">v1.0</span>
          </div>
          <p className="text-[11px] text-zinc-400 font-medium flex items-center gap-1 mt-0.5">
            {isAdmin ? (
              <span className="text-emerald-400 flex items-center gap-1">
                <ShieldCheck className="w-3 h-3" /> Admin Gateway
              </span>
            ) : (
              <span>LLM Gateway</span>
            )}
          </p>
        </div>
      </div>

      {/* Navigation Groups */}
      <nav className="flex-1 px-3 py-4 overflow-y-auto custom-scrollbar">
        {renderNavGroup('Analytics', ANALYTICS_NAV)}
        {renderNavGroup('Developer', DEV_NAV)}
        {isAdmin && renderNavGroup('Administration', ADMIN_NAV)}
      </nav>

      {/* User Footer */}
      <div className="p-4 border-t border-white/[0.08] bg-white/[0.01]">
        <div className="flex items-center justify-between gap-2 p-2 rounded-xl bg-zinc-900/60 border border-white/[0.06]">
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="w-7 h-7 rounded-lg bg-violet-500/20 border border-violet-500/30 flex items-center justify-center text-violet-300 text-xs font-semibold shrink-0">
              {email ? email.charAt(0).toUpperCase() : <User className="w-3.5 h-3.5" />}
            </div>
            <div className="min-w-0">
              <p className="text-xs font-medium text-zinc-200 truncate">{email || 'Authenticated User'}</p>
              <p className="text-[10px] text-zinc-400 capitalize">{isAdmin ? 'Administrator' : 'Standard User'}</p>
            </div>
          </div>
          {onLogout && (
            <button
              type="button"
              onClick={onLogout}
              className="p-1.5 text-zinc-400 hover:text-rose-400 hover:bg-rose-500/10 rounded-lg transition-colors shrink-0"
              title="Sign Out"
            >
              <LogOut className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>
    </aside>
  )
}
