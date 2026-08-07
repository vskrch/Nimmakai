import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '../../lib/utils'

/**
 * Badge — keeps the existing Potato variant set (ok/err/warn/accent/free/fast/
 * indigo/cyan/purple/default) plus shadcn aliases (secondary/outline/destructive).
 */
const badgeVariants = cva(
  'inline-flex items-center px-2.5 py-1 rounded-full text-[11px] font-medium tracking-wide gap-1.5 shrink-0 border',
  {
    variants: {
      variant: {
        ok: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
        err: 'bg-rose-500/10 text-rose-400 border-rose-500/20',
        warn: 'bg-amber-500/10 text-amber-300 border-amber-500/20',
        accent:
          'bg-violet-500/10 text-violet-300 border-violet-500/25 shadow-[0_0_12px_rgba(139,92,246,0.15)]',
        free: 'bg-emerald-500/10 text-emerald-300 border-emerald-500/20',
        fast: 'bg-blue-500/10 text-blue-300 border-blue-500/20',
        indigo: 'bg-indigo-500/10 text-indigo-300 border-indigo-500/20',
        cyan: 'bg-cyan-500/10 text-cyan-300 border-cyan-500/20',
        purple: 'bg-fuchsia-500/10 text-fuchsia-300 border-fuchsia-500/20',
        default: 'bg-white/[0.06] text-zinc-300 border-white/[0.08]',
        // shadcn aliases
        secondary: 'bg-white/[0.06] text-zinc-300 border-white/[0.08]',
        outline: 'text-zinc-300 border-zinc-700',
        destructive: 'bg-rose-500/10 text-rose-400 border-rose-500/20',
      },
    },
    defaultVariants: { variant: 'default' },
  },
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />
}

export { Badge, badgeVariants }