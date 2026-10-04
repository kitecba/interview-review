import type { ReactNode } from 'react'

interface SpinnerProps {
  className?: string
}

export function Spinner({ className = 'size-5' }: SpinnerProps) {
  return (
    <svg
      className={`animate-spin text-ink-faint ${className}`}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <circle
        className="opacity-20"
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="3"
      />
      <path
        className="opacity-90"
        fill="currentColor"
        d="M12 2a10 10 0 0 1 10 10h-3a7 7 0 0 0-7-7V2z"
      />
    </svg>
  )
}

export function LoadingState({ label = '加载中…' }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2.5 py-16 text-sm text-ink-faint">
      <Spinner />
      <span>{label}</span>
    </div>
  )
}

interface ErrorStateProps {
  title?: string
  message: string
  onRetry?: () => void
  retryLabel?: string
}

export function ErrorState({
  title = '加载失败',
  message,
  onRetry,
  retryLabel = '重试',
}: ErrorStateProps) {
  return (
    <div className="border border-bad/30 bg-bad-soft p-5">
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-bad text-paper-raised">
          <svg viewBox="0 0 20 20" fill="currentColor" className="size-3.5">
            <path
              fillRule="evenodd"
              d="M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM9 6a1 1 0 1 1 2 0v4a1 1 0 1 1-2 0V6zm1 9a1.25 1.25 0 1 0 0-2.5A1.25 1.25 0 0 0 10 15z"
              clipRule="evenodd"
            />
          </svg>
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-bad">{title}</p>
          <p className="mt-1 text-sm break-words text-ink-soft">{message}</p>
          {onRetry && (
            <button
              type="button"
              onClick={onRetry}
              className="mt-3 rounded-sm border border-bad/40 bg-paper-raised px-3 py-1.5 text-xs font-medium text-bad transition-colors hover:bg-bad-soft"
            >
              {retryLabel}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

interface EmptyStateProps {
  title: string
  description?: string
  icon?: ReactNode
}

export function EmptyState({ title, description, icon }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center border border-dashed border-rule-strong bg-paper-raised px-6 py-16 text-center">
      {icon && <div className="mb-3 text-ink-faint">{icon}</div>}
      <p className="text-sm font-medium text-ink">{title}</p>
      {description && (
        <p className="mt-1 max-w-md text-sm text-ink-faint">{description}</p>
      )}
    </div>
  )
}
