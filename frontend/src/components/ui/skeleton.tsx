import { cn } from '../../lib/utils'

/**
 * Skeleton primitive — single shimmering block.
 * The composite `Skeleton` with lines/cards variants lives in ui.tsx (facade)
 * and consumes this primitive.
 */
function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('animate-pulse rounded-md bg-white/[0.08]', className)} {...props} />
}

export { Skeleton as SkeletonPrimitive }