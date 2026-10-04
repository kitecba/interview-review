import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, ApiError } from '../api/client'
import type { InterviewListItem } from '../api/types'
import { useApi } from '../hooks/useApi'
import { Badge } from '../components/Badge'
import { ProgressBar } from '../components/ProgressBar'
import { EmptyState, ErrorState, LoadingState } from '../components/States'
import { UploadPanel } from '../components/UploadPanel'
import { formatRelative, formatScore } from '../lib/format'
import { interviewStatusMeta, scoreTextColor } from '../lib/status'

interface ProgressEntry {
  progress: number | null
  message: string | null
}

function isActive(item: InterviewListItem): boolean {
  return item.status === 'processing' || item.status === 'created'
}

/**
 * 对着「处理中 / 待处理」的条目轮询进度。列表 API 不带 progress，
 * 因此额外拉取每条详情里的 run；同时顺带刷新整个列表。
 */
function useInterviewProgress(activeKey: string, reload: () => void) {
  const [progress, setProgress] = useState<Record<string, ProgressEntry>>({})
  const ids = useMemo(() => (activeKey ? activeKey.split('|') : []), [activeKey])
  const reloadRef = useRef(reload)
  reloadRef.current = reload

  useEffect(() => {
    if (ids.length === 0) {
      setProgress({})
      return
    }
    let cancelled = false

    async function tick() {
      reloadRef.current()
      const results = await Promise.all(
        ids.map(async (id) => {
          try {
            const detail = await api.getInterview(id)
            return {
              id,
              entry: {
                progress: detail.run?.progress ?? null,
                message: detail.run?.message ?? null,
              } satisfies ProgressEntry,
            }
          } catch {
            return { id, entry: { progress: null, message: null } satisfies ProgressEntry }
          }
        }),
      )
      if (cancelled) return
      const next: Record<string, ProgressEntry> = {}
      for (const r of results) next[r.id] = r.entry
      setProgress(next)
    }

    void tick()
    const timer = window.setInterval(() => void tick(), 3000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [ids])

  return progress
}

export function InterviewListPage() {
  const navigate = useNavigate()
  const { data, error, loading, reload } = useApi(() => api.listInterviews(), [])
  const [deleting, setDeleting] = useState<string | null>(null)

  const items = useMemo(() => data ?? [], [data])
  const activeKey = useMemo(
    () => items.filter(isActive).map((i) => i.id).join('|'),
    [items],
  )
  const progress = useInterviewProgress(activeKey, reload)

  async function handleDelete(item: InterviewListItem) {
    if (!window.confirm(`确定删除「${item.title}」？该操作不可撤销。`)) return
    setDeleting(item.id)
    try {
      await api.deleteInterview(item.id)
      reload()
    } catch (err) {
      window.alert(err instanceof ApiError ? err.message : '删除失败')
    } finally {
      setDeleting(null)
    }
  }

  function openItem(item: InterviewListItem) {
    if (item.status === 'done' && item.report_version != null) {
      navigate(`/interviews/${item.id}/report`)
    } else {
      navigate(`/interviews/${item.id}`)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-lg font-semibold tracking-tight text-neutral-900">
          面试列表
        </h1>
        <p className="mt-1 text-sm text-neutral-500">
          上传面试录音，自动完成转写、切分、评分与整体复盘。
        </p>
      </div>

      <UploadPanel onCreated={(id) => navigate(`/interviews/${id}`)} />

      <section>
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold text-neutral-900">
            全部面试
            {items.length > 0 && (
              <span className="ml-2 font-normal text-neutral-400">
                {items.length}
              </span>
            )}
          </h2>
          <button
            type="button"
            onClick={reload}
            className="rounded-md border border-neutral-300 bg-white px-2.5 py-1 text-xs font-medium text-neutral-600 transition-colors hover:bg-neutral-50"
          >
            刷新
          </button>
        </div>

        {loading && !data ? (
          <LoadingState label="正在加载面试列表…" />
        ) : error ? (
          <ErrorState message={error.message} onRetry={reload} />
        ) : items.length === 0 ? (
          <EmptyState
            title="还没有面试记录"
            description="在上方拖入一段面试录音，开始你的第一次复盘。"
          />
        ) : (
          <div className="overflow-hidden rounded-xl border border-neutral-200 bg-white">
            <div className="grid grid-cols-[minmax(0,1fr)_128px_96px_132px_96px] items-center gap-4 border-b border-neutral-200 bg-neutral-50/80 px-4 py-2.5 text-xs font-medium text-neutral-500">
              <span>标题</span>
              <span>状态</span>
              <span className="text-right">总分</span>
              <span>创建时间</span>
              <span className="text-right">操作</span>
            </div>
            <ul className="divide-y divide-neutral-100">
              {items.map((item) => {
                const meta = interviewStatusMeta(item.status)
                const run = progress[item.id]
                const active = isActive(item)
                return (
                  <li
                    key={item.id}
                    onClick={() => openItem(item)}
                    className="grid cursor-pointer grid-cols-[minmax(0,1fr)_128px_96px_132px_96px] items-center gap-4 px-4 py-3 transition-colors hover:bg-neutral-50"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium text-neutral-900">
                        {item.title}
                      </p>
                      <p className="mt-0.5 truncate text-xs text-neutral-400">
                        {[item.company, item.position]
                          .filter(Boolean)
                          .join(' · ') || '未填写公司与岗位'}
                      </p>
                    </div>

                    <div className="min-w-0">
                      <Badge
                        tone={meta.tone}
                        dot
                        pulse={item.status === 'processing'}
                      >
                        {meta.label}
                      </Badge>
                      {active && (
                        <div className="mt-1.5">
                          <ProgressBar
                            value={
                              item.status === 'processing'
                                ? (run?.progress ?? null)
                                : null
                            }
                          />
                          {item.status === 'processing' && run?.message && (
                            <p className="mt-1 truncate text-[11px] text-neutral-400">
                              {run.message}
                            </p>
                          )}
                        </div>
                      )}
                    </div>

                    <div className="text-right">
                      <span
                        className={`text-sm font-semibold tabular-nums ${scoreTextColor(item.overall_score)}`}
                      >
                        {formatScore(item.overall_score)}
                      </span>
                    </div>

                    <span className="text-xs text-neutral-500">
                      {formatRelative(item.created_at)}
                    </span>

                    <div className="flex items-center justify-end gap-1.5">
                      {item.status === 'done' && item.report_version != null && (
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation()
                            navigate(`/interviews/${item.id}/report`)
                          }}
                          className="rounded-md border border-neutral-300 bg-white px-2 py-1 text-xs font-medium text-neutral-700 transition-colors hover:bg-neutral-50"
                        >
                          报告
                        </button>
                      )}
                      <button
                        type="button"
                        disabled={deleting === item.id}
                        onClick={(e) => {
                          e.stopPropagation()
                          void handleDelete(item)
                        }}
                        className="rounded-md px-2 py-1 text-xs text-neutral-400 transition-colors hover:bg-red-50 hover:text-red-600 disabled:opacity-50"
                      >
                        删除
                      </button>
                    </div>
                  </li>
                )
              })}
            </ul>
          </div>
        )}
      </section>
    </div>
  )
}
