import React, { type ReactNode, useState } from 'react'
import { clsx } from 'clsx'
import { Check, Copy, X } from 'lucide-react'

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={clsx(
      'bg-zinc-900/60 backdrop-blur-xl border border-white/[0.08] rounded-2xl overflow-hidden shadow-[0_8px_32px_rgba(0,0,0,0.36)] transition-all hover:border-white/[0.14]',
      className
    )}>
      {children}
    </div>
  )
}

export function CardHeader({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className={clsx('px-6 py-4 border-b border-white/[0.08] flex justify-between items-center bg-white/[0.01]', className)}>
      {children}
    </div>
  )
}

export function CardBody({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={clsx('p-6', className)}>{children}</div>
}

export function CardFooter({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={clsx('px-6 py-3.5 border-t border-white/[0.08] bg-white/[0.01] flex items-center justify-between', className)}>{children}</div>
}

export function Badge({
  variant = 'default',
  children,
  className
}: {
  variant?: 'ok' | 'err' | 'warn' | 'accent' | 'free' | 'fast' | 'default' | 'indigo' | 'cyan' | 'purple'
  children: ReactNode
  className?: string
}) {
  const colors: Record<string, string> = {
    ok: 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20',
    err: 'bg-rose-500/10 text-rose-400 border border-rose-500/20',
    warn: 'bg-amber-500/10 text-amber-300 border border-amber-500/20',
    accent: 'bg-violet-500/10 text-violet-300 border border-violet-500/25 shadow-[0_0_12px_rgba(139,92,246,0.15)]',
    free: 'bg-emerald-500/10 text-emerald-300 border border-emerald-500/20',
    fast: 'bg-blue-500/10 text-blue-300 border border-blue-500/20',
    indigo: 'bg-indigo-500/10 text-indigo-300 border border-indigo-500/20',
    cyan: 'bg-cyan-500/10 text-cyan-300 border border-cyan-500/20',
    purple: 'bg-fuchsia-500/10 text-fuchsia-300 border border-fuchsia-500/20',
    default: 'bg-white/[0.06] text-zinc-300 border border-white/[0.08]',
  }
  return (
    <span className={clsx('inline-flex items-center px-2.5 py-1 rounded-full text-[11px] font-medium tracking-wide gap-1.5 shrink-0', colors[variant], className)}>
      {children}
    </span>
  )
}

export function StatusDot({ ok, pulse = true }: { ok: boolean; pulse?: boolean }) {
  return (
    <span className="relative flex h-2 w-2 shrink-0">
      {pulse && (
        <span
          className={clsx(
            'animate-ping absolute inline-flex h-full w-full rounded-full opacity-75',
            ok ? 'bg-emerald-400' : 'bg-rose-400'
          )}
        />
      )}
      <span
        className={clsx(
          'relative inline-flex rounded-full h-2 w-2',
          ok ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.8)]' : 'bg-rose-500 shadow-[0_0_8px_rgba(244,63,94,0.8)]'
        )}
      />
    </span>
  )
}

export function Button({
  children,
  variant = 'default',
  size = 'md',
  className,
  disabled,
  ...props
}: {
  children: ReactNode
  variant?: 'default' | 'primary' | 'danger' | 'ghost' | 'outline' | 'secondary'
  size?: 'xs' | 'sm' | 'md' | 'lg'
  className?: string
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const variants = {
    default: 'bg-zinc-800/80 border border-white/[0.1] text-zinc-200 hover:bg-zinc-700/80 hover:text-white hover:border-white/20 active:scale-[0.98]',
    primary: 'bg-gradient-to-r from-violet-600 via-violet-500 to-fuchsia-500 text-white font-semibold shadow-[0_0_20px_rgba(139,92,246,0.35)] hover:shadow-[0_0_25px_rgba(139,92,246,0.5)] hover:brightness-110 active:scale-[0.98]',
    secondary: 'bg-violet-500/10 border border-violet-500/25 text-violet-300 hover:bg-violet-500/20 hover:border-violet-500/40 active:scale-[0.98]',
    danger: 'bg-rose-500/10 border border-rose-500/25 text-rose-300 hover:bg-rose-500/20 hover:border-rose-500/40 active:scale-[0.98]',
    ghost: 'bg-transparent text-zinc-400 hover:text-white hover:bg-white/[0.05] border border-transparent',
    outline: 'bg-transparent border border-zinc-700 text-zinc-300 hover:border-zinc-500 hover:text-white',
  }
  const sizes = {
    xs: 'px-2 py-1 text-[11px] rounded-md gap-1',
    sm: 'px-3 py-1.5 text-xs rounded-lg gap-1.5',
    md: 'px-4 py-2 text-[13px] rounded-xl gap-2',
    lg: 'px-5 py-2.5 text-sm rounded-xl gap-2.5',
  }
  return (
    <button
      disabled={disabled}
      className={clsx(
        'font-medium cursor-pointer transition-all duration-150 inline-flex items-center justify-center disabled:opacity-50 disabled:pointer-events-none disabled:cursor-not-allowed select-none',
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
        'bg-zinc-950/80 border border-white/[0.1] text-zinc-100 px-3.5 py-2.5 rounded-xl text-[13px] w-full transition-all duration-150 focus:outline-none focus:border-violet-500 focus:ring-1 focus:ring-violet-500 placeholder:text-zinc-500 font-sans',
        className
      )}
      {...props}
    />
  )
}

export function Select({ className, children, ...props }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={clsx(
        'bg-zinc-950/80 border border-white/[0.1] text-zinc-100 px-3.5 py-2.5 rounded-xl text-[13px] w-full transition-all duration-150 focus:outline-none focus:border-violet-500 focus:ring-1 focus:ring-violet-500 cursor-pointer font-sans',
        className
      )}
      {...props}
    >
      {children}
    </select>
  )
}

export function Textarea({ className, ...props }: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      className={clsx(
        'bg-zinc-950/80 border border-white/[0.1] text-zinc-100 px-3.5 py-2.5 rounded-xl text-[13px] w-full transition-all duration-150 focus:outline-none focus:border-violet-500 focus:ring-1 focus:ring-violet-500 placeholder:text-zinc-500 font-mono resize-y',
        className
      )}
      {...props}
    />
  )
}

export function StatBox({
  label,
  value,
  sub,
  color,
  icon: Icon,
  trend
}: {
  label: string
  value: string | number
  sub?: string
  color?: string
  icon?: React.ComponentType<{ className?: string }>
  trend?: { value: string; positive?: boolean }
}) {
  return (
    <div className="bg-zinc-900/60 backdrop-blur-xl border border-white/[0.08] rounded-2xl p-5 flex flex-col justify-between relative overflow-hidden group hover:border-white/[0.15] transition-all duration-200 shadow-[0_4px_20px_rgba(0,0,0,0.2)]">
      <div className="absolute -top-12 -right-12 w-28 h-28 bg-gradient-to-br from-violet-500/10 via-fuchsia-500/5 to-transparent rounded-full blur-2xl group-hover:scale-125 transition-transform duration-500 pointer-events-none" />
      <div className="flex items-center justify-between gap-2 mb-3">
        <span className="text-[11px] text-zinc-400 uppercase tracking-wider font-semibold">{label}</span>
        {Icon && (
          <div className="p-2 rounded-xl bg-white/[0.04] border border-white/[0.06] text-zinc-400 group-hover:text-violet-400 group-hover:bg-violet-500/10 transition-colors">
            <Icon className="w-4 h-4" />
          </div>
        )}
      </div>
      <div className="flex items-baseline justify-between gap-2">
        <span className={clsx('text-2xl font-bold tracking-tight', color || 'text-white')}>{value}</span>
        {trend && (
          <span className={clsx('text-xs font-semibold px-2 py-0.5 rounded-md', trend.positive ? 'bg-emerald-500/10 text-emerald-400' : 'bg-rose-500/10 text-rose-400')}>
            {trend.value}
          </span>
        )}
      </div>
      {sub && <span className="text-xs text-zinc-400 mt-2 font-medium">{sub}</span>}
    </div>
  )
}

export function Toast({ message, type, onDismiss }: { message: string; type: 'ok' | 'err'; onDismiss: () => void }) {
  if (!message) return null
  return (
    <div className="fixed bottom-6 right-6 bg-zinc-900/95 backdrop-blur-xl border border-white/[0.12] px-5 py-3.5 rounded-2xl text-[13px] z-[100] flex items-center gap-3 shadow-[0_20px_50px_rgba(0,0,0,0.6)] animate-[fadeIn_0.25s_ease-out]">
      <StatusDot ok={type === 'ok'} />
      <span className="text-zinc-100 font-medium">{message}</span>
      <button onClick={onDismiss} className="ml-2 text-zinc-500 hover:text-white transition-colors">
        <X className="w-4 h-4" />
      </button>
    </div>
  )
}

export function Spinner() {
  return (
    <div className="flex items-center justify-center py-16">
      <div className="relative w-8 h-8">
        <div className="w-8 h-8 border-2 border-violet-500/20 border-t-violet-500 rounded-full animate-spin" />
        <div className="absolute inset-0 w-8 h-8 border-2 border-fuchsia-500/10 border-b-fuchsia-500 rounded-full animate-spin [animation-duration:1.5s]" />
      </div>
    </div>
  )
}

export function Modal({
  isOpen,
  onClose,
  title,
  children
}: {
  isOpen: boolean
  onClose: () => void
  title: string
  children: ReactNode
}) {
  if (!isOpen) return null

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="fixed inset-0 bg-black/70 backdrop-blur-md transition-opacity" onClick={onClose} />
      <div className="relative bg-zinc-900 border border-white/[0.12] rounded-2xl w-full max-w-lg shadow-[0_25px_60px_rgba(0,0,0,0.8)] overflow-hidden z-10 animate-[fadeIn_0.2s_ease-out]">
        <div className="px-6 py-4 border-b border-white/[0.08] flex items-center justify-between">
          <h3 className="text-base font-semibold text-white">{title}</h3>
          <button onClick={onClose} className="p-1 rounded-lg text-zinc-400 hover:text-white hover:bg-white/[0.06] transition-colors">
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="p-6 max-h-[80vh] overflow-y-auto">{children}</div>
      </div>
    </div>
  )
}

export function CopyButton({ text, className }: { text: string; className?: string }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = () => {
    navigator.clipboard.writeText(text)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  return (
    <button
      onClick={handleCopy}
      className={clsx(
        'p-1.5 rounded-lg border border-white/[0.08] bg-white/[0.04] text-zinc-400 hover:text-white hover:bg-white/[0.08] transition-all active:scale-95',
        className
      )}
      title="Copy to clipboard"
    >
      {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
    </button>
  )
}

export function CodeBlock({ code, language = 'bash' }: { code: string; language?: string }) {
  return (
    <div className="relative bg-zinc-950 border border-white/[0.08] rounded-xl p-4 font-mono text-xs text-zinc-200 overflow-x-auto group">
      <div className="absolute top-3 right-3 opacity-0 group-hover:opacity-100 transition-opacity">
        <CopyButton text={code} />
      </div>
      {language && <div className="text-[10px] text-zinc-500 uppercase tracking-widest mb-2 font-semibold select-none">{language}</div>}
      <pre className="whitespace-pre-wrap break-all">{code}</pre>
    </div>
  )
}
