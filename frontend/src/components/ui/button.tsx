import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '../../lib/utils'

const buttonVariants = cva(
  'inline-flex items-center justify-center whitespace-nowrap rounded-xl text-[13px] font-medium transition-all duration-150 disabled:pointer-events-none disabled:opacity-50 disabled:cursor-not-allowed select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background cursor-pointer',
  {
    variants: {
      variant: {
        default:
          'bg-zinc-800/80 border border-white/[0.1] text-zinc-200 hover:bg-zinc-700/80 hover:text-white hover:border-white/20 active:scale-[0.98]',
        primary:
          'bg-gradient-to-r from-violet-600 via-violet-500 to-fuchsia-500 text-white font-semibold shadow-[0_0_20px_rgba(139,92,246,0.35)] hover:shadow-[0_0_25px_rgba(139,92,246,0.5)] hover:brightness-110 active:scale-[0.98]',
        secondary:
          'bg-violet-500/10 border border-violet-500/25 text-violet-300 hover:bg-violet-500/20 hover:border-violet-500/40 active:scale-[0.98]',
        destructive:
          'bg-rose-500/10 border border-rose-500/25 text-rose-300 hover:bg-rose-500/20 hover:border-rose-500/40 active:scale-[0.98]',
        danger:
          'bg-rose-500/10 border border-rose-500/25 text-rose-300 hover:bg-rose-500/20 hover:border-rose-500/40 active:scale-[0.98]',
        ghost:
          'bg-transparent text-zinc-400 hover:text-white hover:bg-white/[0.05] border border-transparent',
        outline:
          'bg-transparent border border-zinc-700 text-zinc-300 hover:border-zinc-500 hover:text-white',
        link:
          'text-violet-400 underline-offset-4 hover:underline bg-transparent border-transparent',
      },
      size: {
        xs: 'px-2 py-1 text-[11px] rounded-md gap-1',
        sm: 'px-3 py-1.5 text-xs rounded-lg gap-1.5',
        md: 'px-4 py-2 rounded-xl gap-2',
        lg: 'px-5 py-2.5 text-sm rounded-xl gap-2.5',
        icon: 'h-9 w-9 p-0 rounded-xl',
      },
    },
    defaultVariants: { variant: 'default', size: 'md' },
  },
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button'
    return (
      <Comp className={cn(buttonVariants({ variant, size }), className)} ref={ref} {...props} />
    )
  },
)
Button.displayName = 'Button'

export { Button, buttonVariants }