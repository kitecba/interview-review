import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'
import { useApi } from '../hooks/useApi'
import { Badge } from '../components/Badge'
import { RadarBars } from '../components/RadarBars'
import { QaTable } from '../components/QaTable'
import { ErrorState, LoadingState } from '../components/States'
import { formatScore } from '../lib/format'
import { confidenceLabel, confidenceTone, scoreTextColor } from '../lib/status'
import type { ReportSummary } from '../api/types'

function BackLink() {
  const { id = '' } = useParams()
  return (
    <Link
      to={`/interviews/${id}`}
      className="inline-flex items-center gap-1.5 text-sm text-neutral-500 transition-colors hover:text-neutral-900"
    >
      <svg viewBox="0 0 20 20" fill="currentColor" className="size-4">
        <path
          fillRule="evenodd"
          d="M12.7 15.3a1 1 0 0 1-1.4 0l-5-5a1 1 0 0 1 0-1.4l5-5a1 1 0 1 1 1.4 1.4L8.42 10l4.28 4.3a1 1 0 0 1 0 1.4z"
          clipRule="evenodd"
        />
      </svg>
      返回面试详情
    </Link>
  )
}

export function ReportPage() {
  const { id = '' } = useParams()
  const report = useApi(() => api.getReport(id), [id])
  const cost = useApi(() => api.getCost(id), [id])

  if (report.loading && !report.data) {
    return <LoadingState label="正在加载复盘报告…" />
  }

  if (report.error) {
    return (
      <div className="space-y-4">
        <BackLink />
        {report.error.isNotFound ? (
          <div className="rounded-lg border border-neutral-200 bg-white p-8 text-center">
            <p className="text-sm font-medium text-neutral-800">
              报告尚未生成
            </p>
            <p className="mt-1 text-sm text-neutral-500">
              该面试可能还在处理中，或报告接口暂不可用。
            </p>
            <Link
              to={`/interviews/${id}`}
              className="mt-4 inline-block rounded-md border border-neutral-300 bg-white px-3 py-1.5 text-sm font-medium text-neutral-700 hover:bg-neutral-50"
            >
              返回查看进度
            </Link>
          </div>
        ) : (
          <ErrorState
            title="加载报告失败"
            message={report.error.message}
            onRetry={report.reload}
          />
        )}
      </div>
    )
  }

  const data = report.data
  const summary: ReportSummary | null = data?.report?.summary ?? null
  const qa = data?.qa ?? []

  if (!summary && qa.length === 0) {
    return (
      <div className="space-y-4">
        <BackLink />
        <div className="rounded-lg border border-neutral-200 bg-white p-8 text-center">
          <p className="text-sm font-medium text-neutral-800">暂无报告内容</p>
          <p className="mt-1 text-sm text-neutral-500">
            报告还在生成中，请稍后再来查看。
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <BackLink />

      {/* 分数头部 */}
      <header className="rounded-xl border border-neutral-200 bg-white p-6">
        <div className="flex items-start gap-8">
          <div className="shrink-0">
            <div className="flex items-baseline gap-1">
              <span
                className={`text-6xl leading-none font-semibold tabular-nums ${scoreTextColor(summary?.overall_score)}`}
              >
                {formatScore(summary?.overall_score ?? data?.report?.overall_score ?? null)}
              </span>
              <span className="text-lg text-neutral-300">/100</span>
            </div>
            <p className="mt-2 text-xs text-neutral-400">综合得分</p>
          </div>
          <div className="min-w-0 flex-1">
            <h1 className="text-lg font-semibold tracking-tight text-neutral-900">
              复盘报告
            </h1>
            {summary?.assessment && (
              <p className="mt-2 text-sm leading-relaxed text-neutral-600">
                {summary.assessment}
              </p>
            )}
            <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-neutral-400">
              {data?.report?.version != null && (
                <span>报告版本 v{data.report.version}</span>
              )}
              {data?.report?.model && <span>模型 {data.report.model}</span>}
              {cost.data && (
                <span>
                  本次成本 ￥{cost.data.total_cost_cny?.toFixed(2)}
                </span>
              )}
            </div>
          </div>
        </div>
      </header>

      {summary && (
        <>
          {/* 能力雷达（横向条形） */}
          <section className="rounded-xl border border-neutral-200 bg-white p-5">
            <h2 className="mb-4 text-sm font-semibold text-neutral-900">
              能力雷达
            </h2>
            <RadarBars data={summary.competency_radar ?? {}} />
          </section>

          {/* 跨题模式 —— 报告核心，视觉上突出 */}
          {summary.recurring_patterns?.length > 0 && (
            <section className="rounded-xl border border-violet-200 border-l-4 border-l-violet-500 bg-violet-50/50 p-5">
              <div className="mb-3 flex items-center gap-2">
                <Badge tone="violet">重点</Badge>
                <h2 className="text-sm font-semibold text-violet-900">
                  跨题反复出现的模式
                </h2>
                <span className="text-xs text-violet-500">
                  单看某一道题发现不了的行为习惯
                </span>
              </div>
              <ul className="grid grid-cols-2 gap-3">
                {summary.recurring_patterns.map((pattern, i) => (
                  <li
                    key={i}
                    className="flex gap-2.5 rounded-lg border border-violet-100 bg-white px-3.5 py-3"
                  >
                    <span className="mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full bg-violet-100 text-[11px] font-semibold text-violet-700">
                      {i + 1}
                    </span>
                    <span className="text-sm leading-relaxed text-neutral-700">
                      {pattern}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/* 优势 / 问题 */}
          <div className="grid grid-cols-2 gap-6">
            <ListCard
              title="最值得保留的优势"
              items={summary.top_strengths}
              tone="green"
            />
            <ListCard
              title="最该改的问题"
              items={summary.critical_weaknesses}
              tone="red"
            />
          </div>

          {/* 复习计划 */}
          {summary.improvement_plan?.length > 0 && (
            <section className="rounded-xl border border-neutral-200 bg-white p-5">
              <h2 className="mb-4 text-sm font-semibold text-neutral-900">
                复习计划
              </h2>
              <div className="grid grid-cols-2 gap-4">
                {summary.improvement_plan.map((plan, i) => (
                  <div
                    key={i}
                    className="rounded-lg border border-neutral-200 bg-neutral-50/60 p-4"
                  >
                    <p className="text-sm font-medium text-neutral-800">
                      {plan.area}
                    </p>
                    <ul className="mt-2 space-y-1.5">
                      {(plan.actions ?? []).map((action, j) => (
                        <li
                          key={j}
                          className="flex gap-2 text-sm leading-relaxed text-neutral-600"
                        >
                          <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-neutral-400" />
                          <span>{action}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            </section>
          )}

          {/* 结果预判 */}
          {summary.predicted_result && (
            <section className="rounded-xl border border-neutral-200 bg-white p-5">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold text-neutral-900">
                  结果预判
                </h2>
                <span className="flex items-center gap-2 text-xs text-neutral-400">
                  把握程度
                  <Badge tone={confidenceTone(summary.predicted_result.confidence)}>
                    {confidenceLabel(summary.predicted_result.confidence)}
                  </Badge>
                </span>
              </div>
              <p className="mt-3 text-base font-medium text-neutral-900">
                {summary.predicted_result.verdict}
              </p>
              {summary.predicted_result.reason && (
                <p className="mt-1.5 text-sm leading-relaxed text-neutral-600">
                  {summary.predicted_result.reason}
                </p>
              )}
            </section>
          )}
        </>
      )}

      {/* 逐题明细 */}
      <section>
        <h2 className="mb-3 text-sm font-semibold text-neutral-900">
          逐题明细
          <span className="ml-2 font-normal text-neutral-400">
            {qa.length} 题
          </span>
        </h2>
        <QaTable items={qa} />
      </section>
    </div>
  )
}

function ListCard({
  title,
  items,
  tone,
}: {
  title: string
  items: string[]
  tone: 'green' | 'red'
}) {
  const dot = tone === 'green' ? 'bg-emerald-500' : 'bg-red-500'
  const border = tone === 'green' ? 'border-emerald-200' : 'border-red-200'
  return (
    <section className={`rounded-xl border ${border} bg-white p-5`}>
      <h2 className="mb-3 text-sm font-semibold text-neutral-900">{title}</h2>
      {!items || items.length === 0 ? (
        <p className="text-sm text-neutral-400">暂无</p>
      ) : (
        <ul className="space-y-2">
          {items.map((item, i) => (
            <li
              key={i}
              className="flex gap-2.5 text-sm leading-relaxed text-neutral-700"
            >
              <span className={`mt-1.5 size-1.5 shrink-0 rounded-full ${dot}`} />
              <span>{item}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
