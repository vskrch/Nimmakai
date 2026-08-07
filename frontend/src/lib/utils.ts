import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

/**
 * shadcn-style className combiner: clsx + tailwind-merge.
 * tailwind-merge resolves conflicting Tailwind classes (last wins),
 * so `cn('px-2', 'px-4')` → 'px-4' instead of both applying.
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}