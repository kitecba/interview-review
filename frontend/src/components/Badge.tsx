import type { ReactNode } from 'react'

export type BadgeTone =
  | 'neutral'
  | 'blue'
  | 'green'
  | 'red'
  | 'amber'
  | 'violet'

const TONE_CLASSES: Record<BadgeTone, string> = {
  neutral: 'bg-muted-soft text-ink-faint ring-rule-strong',
  blue: 'bg-warn-soft text-warn ring-warn/30',
  green: 'bg-ok-soft text-ok ring-ok/30',
  red: 'bg-bad-soft text-bad ring-bad/30',
  amber: 'bg-warn-soft text-warn ring-warn/30',
  violet: 'bg-paper-sunken text-ink-soft ring-rule-strong',
}

const DOT_CLASSES: Record<BadgeTone, string> = {
  neutral: 'bg-ink-faint',
  blue: 'bg-warn',
  green: 'bg-ok',
  red: 'bg-bad',
  amber: 'bg-warn',
  violet: 'bg-ink-faint',
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
      className={`inline-flex items-center gap-1.5 rounded-sm px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${TONE_CLASSES[tone]} ${className}`}
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
