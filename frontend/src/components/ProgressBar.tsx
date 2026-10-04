import { clamp } from '../lib/format'

interface ProgressBarProps {
  /** 0-100。传 null 表示不确定进度（跑马灯）。 */
  value: number | null
  className?: string
  tone?: string
}

export function ProgressBar({ value, className = '', tone = 'bg-blue-500' }: ProgressBarProps) {
  if (value == null) {
    return (
      <div className={`h-1.5 w-full overflow-hidden rounded-full bg-neutral-200 ${className}`}>
        <div className={`h-full w-1/3 rounded-full ${tone} animate-indeterminate`} />
      </div>
    )
  }
  const pct = clamp(value, 0, 100)
  return (
    <div className={`h-1.5 w-full overflow-hidden rounded-full bg-neutral-200 ${className}`}>
      <div
        className={`h-full rounded-full transition-[width] duration-500 ease-out ${tone}`}
        style={{ width: `${pct}%` }}
      />
    </div>
  )
}
