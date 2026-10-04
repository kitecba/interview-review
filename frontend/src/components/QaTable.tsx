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
      <p className="rounded-md border border-dashed border-neutral-300 bg-white px-4 py-8 text-center text-sm text-neutral-500">
        暂无逐题明细。
      </p>
    )
  }

  const colSpan = 3 + dimensionKeys.length

  return (
    <div className="overflow-hidden rounded-xl border border-neutral-200 bg-white">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-neutral-200 bg-neutral-50/80 text-xs text-neutral-500">
            <th className="w-16 px-4 py-2.5 text-left font-medium">题号</th>
            <th className="px-3 py-2.5 text-left font-medium">主题</th>
            <th className="w-20 px-3 py-2.5 text-right font-medium">总分</th>
            {dimensionKeys.map((key) => (
              <th
                key={key}
                className="w-24 px-3 py-2.5 text-right font-medium"
                title={key}
              >
                {key}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-neutral-100">
          {items.map((qa) => {
            const isOpen = expanded.has(qa.seq)
            return (
              <Fragment key={qa.seq}>
                <tr
                  onClick={() => toggle(qa.seq)}
                  className="cursor-pointer transition-colors hover:bg-neutral-50"
                >
                  <td className="px-4 py-2.5 whitespace-nowrap text-neutral-500 tabular-nums">
                    <span className="flex items-center gap-1">
                      <svg
                        viewBox="0 0 20 20"
                        fill="currentColor"
                        className={`size-3 shrink-0 text-neutral-400 transition-transform ${isOpen ? 'rotate-90' : ''}`}
                      >
                        <path d="M7 5l6 5-6 5V5z" />
                      </svg>
                      {qa.seq}
                    </span>
                  </td>
                  <td className="px-3 py-2.5">
                    <span className="flex min-w-0 items-center gap-2">
                      <span className="truncate text-neutral-800">
                        {qa.topic || '未命名主题'}
                      </span>
                      {qa.is_followup && <Badge tone="violet">追问</Badge>}
                      {qa.is_off_topic && <Badge tone="amber">偏题</Badge>}
                    </span>
                  </td>
                  <td className="px-3 py-2.5 text-right font-semibold text-neutral-800 tabular-nums">
                    {formatScore(qa.analysis?.overall_score ?? null)}
                  </td>
                  {dimensionKeys.map((key) => (
                    <td
                      key={key}
                      className="px-3 py-2.5 text-right text-neutral-600 tabular-nums"
                    >
                      {qa.analysis?.dimension_scores?.[key] != null
                        ? formatScore(qa.analysis.dimension_scores[key])
                        : '—'}
                    </td>
                  ))}
                </tr>
                {isOpen && (
                  <tr className="bg-neutral-50/60">
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
        <p className="text-sm text-neutral-500">该题尚未生成分析。</p>
      ) : (
        <>
          <div>
            <SectionLabel>维度得分</SectionLabel>
            <div className="mt-2 grid grid-cols-2 gap-x-8 gap-y-2">
              {Object.entries(analysis.dimension_scores ?? {}).map(
                ([name, value]) => {
                  const scale = dimensionScale(value)
                  const pct = Math.max(0, Math.min(100, (value / scale) * 100))
                  return (
                    <div
                      key={name}
                      className="grid grid-cols-[104px_minmax(0,1fr)_40px] items-center gap-2"
                    >
                      <span
                        className="truncate text-xs text-neutral-600"
                        title={name}
                      >
                        {name}
                      </span>
                      <span className="h-1.5 w-full overflow-hidden rounded-full bg-neutral-200">
                        <span
                          className="block h-full rounded-full bg-neutral-700"
                          style={{ width: `${pct}%` }}
                        />
                      </span>
                      <span className="text-right text-xs font-medium text-neutral-700 tabular-nums">
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
              <p className="mt-1.5 text-sm leading-relaxed text-neutral-700">
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
                    className="rounded-md border border-neutral-200 bg-white px-3 py-2"
                  >
                    <p className="text-sm font-medium text-neutral-800">
                      {kp.title}
                    </p>
                    {kp.detail && (
                      <p className="mt-0.5 text-xs leading-relaxed text-neutral-500">
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
                    className="rounded-full border border-neutral-200 bg-white px-3 py-1 text-xs text-neutral-600"
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
  return (
    <p className="text-[11px] font-medium tracking-wide text-neutral-400">
      {children}
    </p>
  )
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
      <p className="text-sm leading-relaxed whitespace-pre-wrap text-neutral-700">
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
      ? 'bg-emerald-500'
      : tone === 'red'
        ? 'bg-red-500'
        : 'bg-blue-500'
  return (
    <div>
      <SectionLabel>{title}</SectionLabel>
      <ul className="mt-2 space-y-1.5">
        {items.map((item, i) => (
          <li
            key={i}
            className="flex gap-2 text-sm leading-relaxed text-neutral-700"
          >
            <span className={`mt-1.5 size-1.5 shrink-0 rounded-full ${dot}`} />
            <span>{item}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
