import * as React from 'react'
import { cn } from '../../lib/utils'

/**
 * Native <select> wrapper — keeps the existing API (children = <option>).
 * shadcn's Radix Select has a different API (value/onValueChange, items array)
 * and would break the 3 pages that use <Select><option>... patterns.
 * We keep the native element styled to match; it's the lazy, correct choice.
 */
const Select = React.forwardRef<HTMLSelectElement, React.SelectHTMLAttributes<HTMLSelectElement>>(
  ({ className, children, ...props }, ref) => {
    return (
      <select
        className={cn(
          'bg-zinc-950/80 border border-white/[0.1] text-zinc-100 px-3.5 py-2.5 rounded-xl text-[13px] w-full transition-all duration-150 focus:outline-none focus:border-violet-500 focus:ring-1 focus:ring-violet-500 cursor-pointer font-sans disabled:cursor-not-allowed disabled:opacity-50',
          className,
        )}
        ref={ref}
        {...props}
      >
        {children}
      </select>
    )
  },
)
Select.displayName = 'Select'

export { Select }