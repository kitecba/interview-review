import { Fragment, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import type { QAItem } from '../api/types'
import { Badge } from './Badge'
import { formatScore } from '../lib/format'

/** 维度分通常是 0-10，兜底兼容 0-100。 */
function dimensionScale(value: number): number {
  return value > 10 ? 100 : 10
}

interface QaTableProps {
  items: QAItem[]
}

export function QaTable({ items }: QaTableProps) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set())

  // 表头维度列取所有题目里的维度并集（保持首次出现顺序），最多 4 列。
  const dimensionKeys = useMemo(() => {
    const keys: string[] = []
    for (const qa of items) {
      const scores = qa.analysis?.dimension_scores
      if (!scores) continue
      for (const key of Object.keys(scores)) {
        if (!keys.includes(key)) keys.push(key)
      }
    }
    return keys.slice(0, 4)
  }, [items])

  function toggle(seq: number) {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(seq)) next.delete(seq)
      else next.add(seq)
      return next
    })
  }

  if (items.length === 0) {
    return (
      <p className="border border-dashed border-rule-strong bg-paper-raised px-4 py-8 text-center text-sm text-ink-faint">
        暂无逐题明细。
      </p>
    )
  }

  const colSpan = 3 + dimensionKeys.length

  return (
    <div className="panel overflow-hidden">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-rule bg-paper-sunken/50">
            <th className="label-cap w-16 px-4 py-2.5 text-left font-medium">题号</th>
            <th className="label-cap px-3 py-2.5 text-left font-medium">主题</th>
            <th className="label-cap w-20 px-3 py-2.5 text-right font-medium">总分</th>
            {dimensionKeys.map((key) => (
              <th
                key={key}
                className="label-cap w-24 px-3 py-2.5 text-right font-medium"
                title={key}
              >
                {key}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-rule">
          {items.map((qa) => {
            const isOpen = expanded.has(qa.seq)
            return (
              <Fragment key={qa.seq}>
                <tr
                  onClick={() => toggle(qa.seq)}
                  className="cursor-pointer transition-colors hover:bg-paper-sunken"
                >
                  <td className="mono tnum px-4 py-2.5 whitespace-nowrap text-ink-faint">
                    <span className="flex items-center gap-1">
                      <svg
                        viewBox="0 0 20 20"
                        fill="currentColor"
                        className={`size-3 shrink-0 text-ink-faint transition-transform ${isOpen ? 'rotate-90' : ''}`}
                      >
                        <path d="M7 5l6 5-6 5V5z" />
                      </svg>
                      {qa.seq}
                    </span>
                  </td>
                  <td className="px-3 py-2.5">
                    <span className="flex min-w-0 items-center gap-2">
                      <span className="truncate text-ink">
                        {qa.topic || '未命名主题'}
                      </span>
                      {qa.is_followup && <Badge tone="violet">追问</Badge>}
                      {qa.is_off_topic && <Badge tone="amber">偏题</Badge>}
                    </span>
                  </td>
                  <td className="display tnum px-3 py-2.5 text-right font-semibold text-ink">
                    {formatScore(qa.analysis?.overall_score ?? null)}
                  </td>
                  {dimensionKeys.map((key) => (
                    <td
                      key={key}
                      className="tnum px-3 py-2.5 text-right text-ink-soft"
                    >
                      {qa.analysis?.dimension_scores?.[key] != null
                        ? formatScore(qa.analysis.dimension_scores[key])
                        : '—'}
                    </td>
                  ))}
                </tr>
                {isOpen && (
                  <tr className="bg-paper-sunken/40">
                    <td colSpan={colSpan} className="px-4 py-4">
                      <QaDetail qa={qa} />
                    </td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function QaDetail({ qa }: { qa: QAItem }) {
  const analysis = qa.analysis
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-4">
        <SpeechBlock role="面试官" text={qa.question_text} tone="violet" />
        <SpeechBlock role="候选人" text={qa.answer_text} tone="blue" />
      </div>

      {!analysis ? (
        <p className="text-sm text-ink-faint">该题尚未生成分析。</p>
      ) : (
        <>
          <div>
            <SectionLabel>维度得分</SectionLabel>
            <div className="mt-2 grid grid-cols-2 gap-x-8 gap-y-2">
              {Object.entries(analysis.dimension_scores ?? {}).map(
                ([name, value], index) => {
                  const scale = dimensionScale(value)
                  const pct = Math.max(0, Math.min(100, (value / scale) * 100))
                  return (
                    <div
                      key={name}
                      className="grid grid-cols-[104px_minmax(0,1fr)_40px] items-center gap-2"
                    >
                      <span
                        className="truncate text-xs text-ink-soft"
                        title={name}
                      >
                        {name}
                      </span>
                      <span className="h-1.5 w-full overflow-hidden bg-paper-sunken">
                        <span
                          className="bar-grow block h-full bg-ink-soft"
                          style={{
                            width: `${pct}%`,
                            animationDelay: `${index * 40}ms`,
                          }}
                        />
                      </span>
                      <span className="display tnum text-right text-xs leading-none text-ink">
                        {formatScore(value)}
                      </span>
                    </div>
                  )
                },
              )}
            </div>
          </div>

          {analysis.summary && (
            <div>
              <SectionLabel>总评</SectionLabel>
              <p className="mt-1.5 text-sm leading-relaxed text-ink-soft">
                {analysis.summary}
              </p>
            </div>
          )}

          <div className="grid grid-cols-2 gap-4">
            <BulletBlock
              title="做对了什么"
              items={analysis.strengths}
              tone="green"
            />
            <BulletBlock
              title="哪里不足"
              items={analysis.weaknesses}
              tone="red"
            />
          </div>

          <BulletBlock
            title="下次可以怎么答"
            items={analysis.improvement}
            tone="blue"
          />

          {analysis.knowledge_points?.length > 0 && (
            <div>
              <SectionLabel>需要补的知识点</SectionLabel>
              <ul className="mt-2 space-y-1.5">
                {analysis.knowledge_points.map((kp, i) => (
                  <li
                    key={i}
                    className="border border-rule bg-paper-raised px-3 py-2"
                  >
                    <p className="text-sm font-medium text-ink">
                      {kp.title}
                    </p>
                    {kp.detail && (
                      <p className="mt-0.5 text-xs leading-relaxed text-ink-faint">
                        {kp.detail}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {analysis.predicted_followups?.length > 0 && (
            <div>
              <SectionLabel>可能的追问</SectionLabel>
              <ul className="mt-2 flex flex-wrap gap-2">
                {analysis.predicted_followups.map((f, i) => (
                  <li
                    key={i}
                    className="rounded-sm border border-rule bg-paper-raised px-3 py-1 text-xs text-ink-soft"
                  >
                    {f}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </div>
  )
}

function SectionLabel({ children }: { children: ReactNode }) {
  return <p className="label-cap">{children}</p>
}

function SpeechBlock({
  role,
  text,
  tone,
}: {
  role: string
  text: string
  tone: 'violet' | 'blue'
}) {
  return (
    <div>
      <div className="mb-1.5">
        <Badge tone={tone}>{role}</Badge>
      </div>
      <p className="text-sm leading-relaxed whitespace-pre-wrap text-ink-soft">
        {text || '（空）'}
      </p>
    </div>
  )
}

function BulletBlock({
  title,
  items,
  tone,
}: {
  title: string
  items: string[]
  tone: 'green' | 'red' | 'blue'
}) {
  if (!items || items.length === 0) return null
  const dot =
    tone === 'green'
      ? 'bg-ok'
      : tone === 'red'
        ? 'bg-bad'
        : 'bg-ink-faint'
  return (
    <div>
      <SectionLabel>{title}</SectionLabel>
      <ul className="mt-2 space-y-1.5">
        {items.map((item, i) => (
          <li
            key={i}
            className="flex gap-2 text-sm leading-relaxed text-ink-soft"
          >
            <span className={`mt-1.5 size-1.5 shrink-0 rounded-full ${dot}`} />
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
