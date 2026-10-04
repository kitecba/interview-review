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
    ring: 'border-rule-strong',
    fill: 'bg-paper-raised',
    text: 'text-ink-faint',
  },
  running: {
    ring: 'border-warn/40',
    fill: 'bg-warn',
    text: 'text-warn',
  },
  done: {
    ring: 'border-ok/40',
    fill: 'bg-ok',
    text: 'text-ok',
  },
  failed: {
    ring: 'border-bad/40',
    fill: 'bg-bad',
    text: 'text-bad',
  },
  skipped: {
    ring: 'border-rule-strong',
    fill: 'bg-rule-strong',
    text: 'text-ink-faint',
  },
  cached: {
    ring: 'border-rule-strong',
    fill: 'bg-rule-strong',
    text: 'text-ink-faint',
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
              <span className="absolute top-5 left-[9px] h-[calc(100%-12px)] w-px bg-rule" />
            )}
            {/* 节点 */}
            <span
              className={`relative z-10 mt-0.5 flex size-[18px] shrink-0 items-center justify-center rounded-full border-2 ${node.ring} ${node.fill}`}
            >
              {display === 'running' && (
                <span className="absolute inset-0 animate-ping rounded-full bg-warn/40" />
              )}
              {display === 'failed' && (
                <svg viewBox="0 0 20 20" fill="white" className="size-3">
                  <path d="M10 5v6m0 3h.01" stroke="white" strokeWidth="2" strokeLinecap="round" />
                </svg>
              )}
            </span>

            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                <span className="text-sm font-medium text-ink">
                  {stage.label}
                </span>
                <span className="mono text-[11px] text-ink-faint">
                  {stage.stage}
                </span>
                <Badge tone={meta.tone as BadgeTone} dot pulse={display === 'running'}>
                  {meta.label}
                </Badge>
                <span className="ml-auto flex items-center gap-3 text-xs text-ink-faint">
                  {stage.attempt > 1 && (
                    <span className="mono tnum" title="尝试次数">第 {stage.attempt} 次</span>
                  )}
                  <span className="mono tnum">
                    {formatDuration(stage.duration_ms)}
                  </span>
                </span>
              </div>

              {(stage.started_at || stage.finished_at) && (
                <p className="mono tnum mt-1 text-[11px] text-ink-faint">
                  {formatDateTime(stage.started_at)} → {formatDateTime(stage.finished_at)}
                </p>
              )}

              {stage.error && (
                <div className="mt-2 border border-bad/30 bg-bad-soft px-3 py-2">
                  <p className="text-xs break-words text-bad">{stage.error}</p>
                  {onRetry && (
                    <button
                      type="button"
                      disabled={retryingStage === stage.stage}
                      onClick={() => onRetry(stage.stage)}
                      className="mt-2 rounded-sm border border-bad/40 bg-paper-raised px-2.5 py-1 text-xs font-medium text-bad transition-colors hover:bg-bad-soft disabled:opacity-60"
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
