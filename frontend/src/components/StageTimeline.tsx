import type { StageInfo } from '../api/types'
import { Badge } from './Badge'
import type { BadgeTone } from './Badge'
import { formatDateTime, formatDuration } from '../lib/format'
import { stageDisplayStatus, stageStatusMeta } from '../lib/status'

interface StageTimelineProps {
  stages: StageInfo[]
  onRetry?: (stage: string) => void
  retryingStage?: string | null
}

const NODE_TONE: Record<string, { ring: string; fill: string; text: string }> = {
  pending: {
    ring: 'border-neutral-300',
    fill: 'bg-white',
    text: 'text-neutral-400',
  },
  running: {
    ring: 'border-blue-400',
    fill: 'bg-blue-500',
    text: 'text-blue-600',
  },
  done: {
    ring: 'border-emerald-400',
    fill: 'bg-emerald-500',
    text: 'text-emerald-600',
  },
  failed: {
    ring: 'border-red-400',
    fill: 'bg-red-500',
    text: 'text-red-600',
  },
  skipped: {
    ring: 'border-neutral-300',
    fill: 'bg-neutral-300',
    text: 'text-neutral-400',
  },
  cached: {
    ring: 'border-neutral-300',
    fill: 'bg-neutral-300',
    text: 'text-neutral-400',
  },
}

export function StageTimeline({
  stages,
  onRetry,
  retryingStage,
}: StageTimelineProps) {
  return (
    <ol className="relative">
      {stages.map((stage, index) => {
        const display = stageDisplayStatus(stage)
        const meta = stageStatusMeta(stage)
        const node = NODE_TONE[display] ?? NODE_TONE.pending
        const isLast = index === stages.length - 1
        return (
          <li key={`${stage.stage}-${index}`} className="relative flex gap-4 pb-5">
            {/* 连接线 */}
            {!isLast && (
              <span className="absolute top-5 left-[9px] h-[calc(100%-12px)] w-px bg-neutral-200" />
            )}
            {/* 节点 */}
            <span
              className={`relative z-10 mt-0.5 flex size-[18px] shrink-0 items-center justify-center rounded-full border-2 ${node.ring} ${node.fill}`}
            >
              {display === 'running' && (
                <span className="absolute inset-0 animate-ping rounded-full bg-blue-400/60" />
              )}
              {display === 'failed' && (
                <svg viewBox="0 0 20 20" fill="white" className="size-3">
                  <path d="M10 5v6m0 3h.01" stroke="white" strokeWidth="2" strokeLinecap="round" />
                </svg>
              )}
            </span>

            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <span className="text-sm font-medium text-neutral-900">
                  {stage.label}
                </span>
                <span className="font-mono text-[11px] text-neutral-300">
                  {stage.stage}
                </span>
                <Badge tone={meta.tone as BadgeTone} dot pulse={display === 'running'}>
                  {meta.label}
                </Badge>
                <span className="ml-auto flex items-center gap-3 text-xs text-neutral-400">
                  {stage.attempt > 1 && (
                    <span title="尝试次数">第 {stage.attempt} 次</span>
                  )}
                  <span className="tabular-nums">
                    {formatDuration(stage.duration_ms)}
                  </span>
                </span>
              </div>

              {(stage.started_at || stage.finished_at) && (
                <p className="mt-1 text-[11px] text-neutral-400">
                  {formatDateTime(stage.started_at)} → {formatDateTime(stage.finished_at)}
                </p>
              )}

              {stage.error && (
                <div className="mt-2 rounded-md border border-red-200 bg-red-50/70 px-3 py-2">
                  <p className="text-xs break-words text-red-700">{stage.error}</p>
                  {onRetry && (
                    <button
                      type="button"
                      disabled={retryingStage === stage.stage}
                      onClick={() => onRetry(stage.stage)}
                      className="mt-2 rounded-md border border-red-300 bg-white px-2.5 py-1 text-xs font-medium text-red-700 transition-colors hover:bg-red-50 disabled:opacity-60"
                    >
                      {retryingStage === stage.stage
                        ? '正在重跑…'
                        : '从该阶段重跑'}
                    </button>
                  )}
                </div>
              )}
            </div>
          </li>
        )
      })}
    </ol>
  )
}
