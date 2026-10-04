import { Link, useParams } from 'react-router-dom'
import { api } from '../api/client'
import { useApi } from '../hooks/useApi'
import { Badge } from '../components/Badge'
import { RadarBars } from '../components/RadarBars'
import { QaTable } from '../components/QaTable'
import { ErrorState, LoadingState } from '../components/States'
import { formatScore } from '../lib/format'
import { confidenceLabel, confidenceTone } from '../lib/status'
import type { ReportSummary } from '../api/types'

function BackLink() {
  const { id = '' } = useParams()
  return (
    <Link
      to={`/interviews/${id}`}
      className="inline-flex items-center gap-1.5 text-sm text-ink-faint transition-colors hover:text-ink"
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
          <div className="panel p-8 text-center">
            <p className="text-sm font-medium text-ink">
              报告尚未生成
            </p>
            <p className="mt-1 text-sm text-ink-faint">
              该面试可能还在处理中，或报告接口暂不可用。
            </p>
            <Link
              to={`/interviews/${id}`}
              className="mt-4 inline-block rounded-sm border border-rule-strong bg-paper-raised px-3 py-1.5 text-sm font-medium text-ink-soft hover:bg-paper-sunken"
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
        <div className="panel p-8 text-center">
          <p className="text-sm font-medium text-ink">暂无报告内容</p>
          <p className="mt-1 text-sm text-ink-faint">
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
      <header className="panel reveal p-6" style={{ animationDelay: '0ms' }}>
        <div className="flex items-start gap-8">
          <div className="shrink-0">
            <div className="flex items-baseline gap-1">
              <span className="display tnum text-6xl leading-none text-seal">
                {formatScore(summary?.overall_score ?? data?.report?.overall_score ?? null)}
              </span>
              <span className="mono text-lg text-ink-faint">/100</span>
            </div>
            <p className="label-cap mt-3">综合得分</p>
          </div>
          <div className="min-w-0 flex-1">
            <h1 className="text-lg font-semibold tracking-tight text-ink">
              复盘报告
            </h1>
            {summary?.assessment && (
              <p className="mt-2 text-sm leading-relaxed text-ink-soft">
                {summary.assessment}
              </p>
            )}
            <div className="mono mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-ink-faint">
              {data?.report?.version != null && (
                <span className="tnum">报告版本 v{data.report.version}</span>
              )}
              {data?.report?.model && <span>模型 {data.report.model}</span>}
              {cost.data && (
                <span className="tnum">
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
          <section
            className="panel reveal p-5"
            style={{ animationDelay: '60ms' }}
          >
            <h2 className="section-head text-sm font-semibold text-ink">
              能力雷达
            </h2>
            <RadarBars data={summary.competency_radar ?? {}} />
          </section>

          {/* 跨题模式 —— 报告核心，用朱印标记 */}
          {summary.recurring_patterns?.length > 0 && (
            <section
              className="panel reveal border-l-2 border-l-seal p-5"
              style={{ animationDelay: '120ms' }}
            >
              <div className="mb-3 flex items-center gap-3">
                <span className="label-cap text-seal">重点</span>
                <h2 className="text-sm font-semibold text-ink">
                  跨题反复出现的模式
                </h2>
                <span className="text-xs text-ink-faint">
                  单看某一道题发现不了的行为习惯
                </span>
              </div>
              <ul className="grid grid-cols-2 gap-3">
                {summary.recurring_patterns.map((pattern, i) => (
                  <li
                    key={i}
                    className="flex gap-2.5 border border-rule bg-paper-raised px-3.5 py-3"
                  >
                    <span className="display flex size-5 shrink-0 items-center justify-center border border-seal/30 text-[11px] leading-none text-seal">
                      {i + 1}
                    </span>
                    <span className="text-sm leading-relaxed text-ink-soft">
                      {pattern}
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/* 优势 / 问题 */}
          <div
            className="reveal grid grid-cols-2 gap-6"
            style={{ animationDelay: '180ms' }}
          >
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
            <section
              className="panel reveal p-5"
              style={{ animationDelay: '240ms' }}
            >
              <h2 className="section-head text-sm font-semibold text-ink">
                复习计划
              </h2>
              <div className="grid grid-cols-2 gap-4">
                {summary.improvement_plan.map((plan, i) => (
                  <div
                    key={i}
                    className="border border-rule bg-paper-sunken/40 p-4"
                  >
                    <p className="text-sm font-medium text-ink">
                      {plan.area}
                    </p>
                    <ul className="mt-2 space-y-1.5">
                      {(plan.actions ?? []).map((action, j) => (
                        <li
                          key={j}
                          className="flex gap-2 text-sm leading-relaxed text-ink-soft"
                        >
                          <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-ink-faint" />
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
            <section
              className="panel reveal p-5"
              style={{ animationDelay: '300ms' }}
            >
              <div className="section-head">
                <h2 className="text-sm font-semibold text-ink">
                  结果预判
                </h2>
                <span className="flex items-center gap-2 text-xs text-ink-faint">
                  把握程度
                  <Badge tone={confidenceTone(summary.predicted_result.confidence)}>
                    {confidenceLabel(summary.predicted_result.confidence)}
                  </Badge>
                </span>
              </div>
              <p className="mt-3 text-base font-medium text-ink">
                {summary.predicted_result.verdict}
              </p>
              {summary.predicted_result.reason && (
                <p className="mt-1.5 text-sm leading-relaxed text-ink-soft">
                  {summary.predicted_result.reason}
                </p>
              )}
            </section>
          )}
        </>
      )}

      {/* 逐题明细 */}
      <section className="reveal" style={{ animationDelay: '300ms' }}>
        <h2 className="section-head text-sm font-semibold text-ink">
          逐题明细
          <span className="mono tnum ml-2 font-normal text-ink-faint">
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
  const dot = tone === 'green' ? 'bg-ok' : 'bg-seal'
  return (
    <section className="panel p-5">
      <h2 className="section-head text-sm font-semibold text-ink">{title}</h2>
      {!items || items.length === 0 ? (
        <p className="text-sm text-ink-faint">暂无</p>
      ) : (
        <ul className="space-y-2">
          {items.map((item, i) => (
            <li
              key={i}
              className="flex gap-2.5 text-sm leading-relaxed text-ink-soft"
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
