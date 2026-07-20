import { clsx } from 'clsx'
import { type ReactNode } from 'react'

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={clsx(
      'bg-white/[0.03] backdrop-blur-xl border border-white/[0.08] rounded-xl overflow-hidden mb-6 shadow-[0_8px_32px_rgba(0,0,0,0.4)] transition-all hover:border-white/[0.12]',
      className
    )}>
      {children}
    </div>
  )
}

export function CardHeader({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={clsx('px-6 py-4 border-b border-white/[0.08] flex justify-between items-center', className)}>
      {children}
    </div>
  )
}

export function CardBody({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={clsx('p-6', className)}>{children}</div>
}

export function Badge({ variant = 'default', children }: { variant?: 'ok' | 'err' | 'accent' | 'free' | 'fast' | 'default'; children: ReactNode }) {
  const colors: Record<string, string> = {
    ok: 'bg-emerald-500/10 text-emerald-400',
    err: 'bg-red-500/10 text-red-400',
    accent: 'bg-violet-500/10 text-violet-300 border border-violet-500/20',
    free: 'bg-emerald-500/[0.12] text-emerald-400 border border-emerald-500/25',
    fast: 'bg-blue-500/[0.12] text-blue-300 border border-blue-500/25',
    default: 'bg-white/10 text-white/70',
  }
  return (
    <span className={clsx('inline-flex items-center px-2.5 py-1 rounded-full text-[11px] font-semibold gap-1.5', colors[variant])}>
      {children}
    </span>
  )
}

export function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span
      className={clsx(
        'w-1.5 h-1.5 rounded-full inline-block',
        ok ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.6)]' : 'bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.6)]'
      )}
    />
  )
}

export function Button({ children, variant = 'default', size = 'md', className, ...props }: {
  children: ReactNode
  variant?: 'default' | 'primary' | 'danger'
  size?: 'sm' | 'md'
  className?: string
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const variants = {
    default: 'bg-white/[0.03] border border-white/[0.08] text-white hover:bg-white/[0.05] hover:border-white/20',
    primary: 'bg-gradient-to-r from-violet-500 to-fuchsia-500 border-none text-white shadow-[0_4px_12px_rgba(139,92,246,0.3)] hover:opacity-90',
    danger: 'bg-transparent border border-red-500/20 text-red-400 hover:bg-red-500/10 hover:border-red-500',
  }
  const sizes = {
    sm: 'px-3 py-1.5 text-xs',
    md: 'px-4 py-2 text-[13px]',
  }
  return (
    <button
      className={clsx(
        'rounded-lg font-medium cursor-pointer transition-all inline-flex items-center gap-2',
        variants[variant],
        sizes[size],
        className
      )}
      {...props}
    >
      {children}
    </button>
  )
}

export function Input({ className, ...props }: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={clsx(
        'bg-black/20 border border-white/[0.08] text-white px-3.5 py-2.5 rounded-lg text-[13px] w-full transition-all focus:outline-none focus:border-violet-500/50 focus:shadow-[0_0_0_2px_rgba(139,92,246,0.1)] placeholder:text-zinc-500 font-[inherit]',
        className
      )}
      {...props}
    />
  )
}

export function StatBox({ label, value, sub, color }: { label: string; value: string | number; sub?: string; color?: string }) {
  return (
    <div className="bg-white/[0.03] border border-white/[0.08] rounded-xl p-5 flex flex-col gap-2 relative overflow-hidden">
      <div className="absolute top-0 right-0 w-16 h-16 bg-gradient-to-r from-violet-500 to-fuchsia-500 opacity-5 rounded-full blur-[20px]" />
      <span className="text-[11px] text-zinc-400 uppercase tracking-[1px] font-semibold">{label}</span>
      <span className={clsx('text-2xl font-bold', color || 'text-white')}>{value}</span>
      {sub && <span className="text-xs text-zinc-500">{sub}</span>}
    </div>
  )
}

export function Toast({ message, type, onDismiss }: { message: string; type: 'ok' | 'err'; onDismiss: () => void }) {
  if (!message) return null
  setTimeout(onDismiss, 3500)
  return (
    <div className="fixed bottom-6 right-6 bg-zinc-900 border border-white/[0.08] px-5 py-3.5 rounded-xl text-[13px] z-[60] flex items-center gap-3 shadow-[0_16px_32px_rgba(0,0,0,0.4)] animate-[fadeIn_0.3s_ease]">
      <span className={clsx('w-2 h-2 rounded-full', type === 'ok' ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.6)]' : 'bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.6)]')} />
      <span>{message}</span>
    </div>
  )
}

export function Spinner() {
  return (
    <div className="flex items-center justify-center py-12">
      <div className="w-6 h-6 border-2 border-violet-500/30 border-t-violet-500 rounded-full animate-spin" />
    </div>
  )
}
