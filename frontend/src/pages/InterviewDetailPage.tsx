import { useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import { useApi, usePolling } from '../hooks/useApi'
import { Badge } from '../components/Badge'
import { ProgressBar } from '../components/ProgressBar'
import { StageTimeline } from '../components/StageTimeline'
import { TranscriptLegendNote, TranscriptView } from '../components/TranscriptView'
import { ErrorState, LoadingState } from '../components/States'
import {
  formatBytes,
  formatDateTime,
  formatDuration,
  formatTimestamp,
} from '../lib/format'
import { interviewStatusMeta } from '../lib/status'

function BackLink() {
  return (
    <Link
      to="/"
      className="inline-flex items-center gap-1.5 text-sm text-neutral-500 transition-colors hover:text-neutral-900"
    >
      <svg viewBox="0 0 20 20" fill="currentColor" className="size-4">
        <path
          fillRule="evenodd"
          d="M12.7 15.3a1 1 0 0 1-1.4 0l-5-5a1 1 0 0 1 0-1.4l5-5a1 1 0 1 1 1.4 1.4L8.42 10l4.28 4.3a1 1 0 0 1 0 1.4z"
          clipRule="evenodd"
        />
      </svg>
      返回列表
    </Link>
  )
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-neutral-200 bg-white px-3 py-2">
      <p className="text-[11px] text-neutral-400">{label}</p>
      <p className="mt-0.5 truncate text-sm font-medium text-neutral-800">{value}</p>
    </div>
  )
}

export function InterviewDetailPage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const [tab, setTab] = useState<'stages' | 'transcript'>('stages')
  const [retryingStage, setRetryingStage] = useState<string | null>(null)
  const [retryError, setRetryError] = useState<string | null>(null)

  const detail = useApi(() => api.getInterview(id), [id])
  const transcript = useApi(
    () => api.getTranscript(id),
    [id, detail.data?.interview.status],
  )

  const data = detail.data
  const shouldPoll = useMemo(() => {
    if (!data) return true
    const status = data.interview.status
    const runStatus = data.run?.status
    return (
      status === 'processing' ||
      status === 'created' ||
      runStatus === 'running' ||
      runStatus === 'pending' ||
      data.stages.some((s) => s.status === 'running' || s.status === 'pending')
    )
  }, [data])

  usePolling(shouldPoll, detail.reload, 2000)

  async function handleRetry(stage: string) {
    setRetryingStage(stage)
    setRetryError(null)
    try {
      await api.createRun(id, stage)
      detail.reload()
    } catch (err) {
      setRetryError(err instanceof ApiError ? err.message : '重跑失败')
    } finally {
      setRetryingStage(null)
    }
  }

  if (detail.loading && !data) {
    return <LoadingState label="正在加载面试详情…" />
  }

  if (detail.error) {
    return (
      <div className="space-y-4">
        <BackLink />
        {detail.error.isNotFound ? (
          <div className="rounded-lg border border-neutral-200 bg-white p-6 text-center">
            <p className="text-sm font-medium text-neutral-800">
              未找到该面试记录
            </p>
            <p className="mt-1 text-sm text-neutral-500">
              记录可能已被删除，或链接有误。
            </p>
          </div>
        ) : (
          <ErrorState
            title="加载面试详情失败"
            message={detail.error.message}
            onRetry={detail.reload}
          />
        )}
      </div>
    )
  }

  if (!data) {
    return (
      <div className="space-y-4">
        <BackLink />
        <ErrorState title="没有数据" message="后端没有返回该面试的信息。" onRetry={detail.reload} />
      </div>
    )
  }

  const { interview, audio, run, stages } = data
  const meta = interviewStatusMeta(interview.status)
  const runningStage = stages.find((s) => s.status === 'running')
  const failedStage = stages.find((s) => s.status === 'failed')

  return (
    <div className="space-y-6">
      <BackLink />

      {/* 头部 */}
      <header className="rounded-xl border border-neutral-200 bg-white p-5">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-3">
              <h1 className="truncate text-xl font-semibold tracking-tight text-neutral-900">
                {interview.title}
              </h1>
              <Badge tone={meta.tone} dot pulse={interview.status === 'processing'}>
                {meta.label}
              </Badge>
            </div>
            <p className="mt-1 text-sm text-neutral-500">
              {[interview.company, interview.position].filter(Boolean).join(' · ') ||
                '未填写公司与岗位'}
            </p>
            <p className="mt-1 text-xs text-neutral-400">
              创建于 {formatDateTime(interview.created_at)}
            </p>
          </div>
          {interview.status === 'done' && (
            <button
              type="button"
              onClick={() => navigate(`/interviews/${id}/report`)}
              className="shrink-0 rounded-md bg-neutral-900 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-neutral-800"
            >
              查看报告
            </button>
          )}
        </div>

        <div className="mt-4 grid grid-cols-4 gap-3">
          <StatCard
            label="音频文件"
            value={audio?.original_filename ?? '—'}
          />
          <StatCard
            label="录音时长"
            value={audio ? formatTimestamp(audio.duration_ms) : '—'}
          />
          <StatCard
            label="文件大小"
            value={audio ? formatBytes(audio.size_bytes) : '—'}
          />
          <StatCard
            label="当前阶段"
            value={
              runningStage?.label ??
              (interview.status === 'done'
                ? '已完成'
                : interview.status === 'failed'
                  ? (failedStage?.label ?? '失败')
                  : '—')
            }
          />
        </div>
      </header>

      {/* 运行进度 */}
      {run && (run.status === 'running' || run.status === 'pending') && (
        <section className="rounded-xl border border-blue-200 bg-blue-50/50 p-4">
          <div className="mb-2 flex items-center justify-between text-sm">
            <span className="font-medium text-blue-800">
              {run.message ?? '正在处理…'}
            </span>
            <span className="tabular-nums text-blue-700">
              {Math.round(run.progress ?? 0)}%
            </span>
          </div>
          <ProgressBar value={run.progress ?? null} />
        </section>
      )}

      {retryError && (
        <ErrorState title="重跑失败" message={retryError} onRetry={() => setRetryError(null)} retryLabel="知道了" />
      )}

      {/* 标签页 */}
      <div className="flex items-center gap-1 border-b border-neutral-200">
        <TabButton active={tab === 'stages'} onClick={() => setTab('stages')}>
          阶段时间线
        </TabButton>
        <TabButton
          active={tab === 'transcript'}
          onClick={() => setTab('transcript')}
        >
          转写文本
        </TabButton>
      </div>

      {tab === 'stages' ? (
        <section className="rounded-xl border border-neutral-200 bg-white p-5">
          {stages.length === 0 ? (
            <p className="py-6 text-center text-sm text-neutral-500">
              暂无阶段信息。
            </p>
          ) : (
            <>
              {failedStage && (
                <div className="mb-4 flex items-center justify-between rounded-md border border-red-200 bg-red-50/60 px-3 py-2 text-xs text-red-700">
                  <span>流程在「{failedStage.label}」阶段失败。</span>
                  <button
                    type="button"
                    disabled={retryingStage === failedStage.stage}
                    onClick={() => handleRetry(failedStage.stage)}
                    className="rounded-md border border-red-300 bg-white px-2.5 py-1 font-medium transition-colors hover:bg-red-50 disabled:opacity-60"
                  >
                    从该阶段重跑
                  </button>
                </div>
              )}
              <StageTimeline
                stages={stages}
                onRetry={handleRetry}
                retryingStage={retryingStage}
              />
            </>
          )}
        </section>
      ) : (
        <section className="space-y-3 rounded-xl border border-neutral-200 bg-white p-5">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-neutral-900">转写文本</h2>
            <TranscriptLegendNote />
          </div>
          {transcript.loading && !transcript.data ? (
            <LoadingState label="正在加载转写文本…" />
          ) : transcript.error ? (
            transcript.error.isNotFound ? (
              <p className="rounded-md border border-dashed border-neutral-300 px-4 py-8 text-center text-sm text-neutral-500">
                转写文本尚未生成（可能还没跑到切分阶段）。
              </p>
            ) : (
              <ErrorState
                title="加载转写文本失败"
                message={transcript.error.message}
                onRetry={transcript.reload}
              />
            )
          ) : (
            <TranscriptView
              speakers={transcript.data?.speakers ?? []}
              segments={transcript.data?.segments ?? []}
            />
          )}
        </section>
      )}

      {/* 耗时摘要 */}
      {run && run.status === 'done' && (
        <p className="text-xs text-neutral-400">
          本次运行已完成
          {stages.reduce((sum, s) => sum + (s.duration_ms ?? 0), 0) > 0 &&
            `，各阶段耗时合计 ${formatDuration(
              stages.reduce((sum, s) => sum + (s.duration_ms ?? 0), 0),
            )}`}
          。
        </p>
      )}
    </div>
  )
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors ${
        active
          ? 'border-neutral-900 text-neutral-900'
          : 'border-transparent text-neutral-500 hover:text-neutral-800'
      }`}
    >
      {children}
    </button>
  )
}
