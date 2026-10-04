import type { ReactNode } from 'react'

export type BadgeTone =
  | 'neutral'
  | 'blue'
  | 'green'
  | 'red'
  | 'amber'
  | 'violet'

const TONE_CLASSES: Record<BadgeTone, string> = {
  neutral: 'bg-neutral-100 text-neutral-600 ring-neutral-200',
  blue: 'bg-blue-50 text-blue-700 ring-blue-200',
  green: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  red: 'bg-red-50 text-red-700 ring-red-200',
  amber: 'bg-amber-50 text-amber-700 ring-amber-200',
  violet: 'bg-violet-50 text-violet-700 ring-violet-200',
}

const DOT_CLASSES: Record<BadgeTone, string> = {
  neutral: 'bg-neutral-400',
  blue: 'bg-blue-500',
  green: 'bg-emerald-500',
  red: 'bg-red-500',
  amber: 'bg-amber-500',
  violet: 'bg-violet-500',
}

interface BadgeProps {
  tone?: BadgeTone
  children: ReactNode
  dot?: boolean
  pulse?: boolean
  className?: string
}

export function Badge({
  tone = 'neutral',
  children,
  dot = false,
  pulse = false,
  className = '',
}: BadgeProps) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${TONE_CLASSES[tone]} ${className}`}
    >
      {dot && (
        <span
          className={`size-1.5 rounded-full ${DOT_CLASSES[tone]} ${pulse ? 'animate-pulse' : ''}`}
        />
      )}
      {children}
    </span>
  )
}
