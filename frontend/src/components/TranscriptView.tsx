import { Fragment, useMemo, useState } from 'react'
import type { ManualRole, Segment, Speaker } from '../api/types'
import { Badge } from './Badge'
import type { BadgeTone } from './Badge'
import { formatTimestamp } from '../lib/format'
import { roleLabel, roleTone } from '../lib/status'

interface TranscriptViewProps {
  speakers: Speaker[]
  segments: Segment[]
  /** 提供了这个回调，说话人徽章才会成为改判入口 */
  onChangeRole?: (speakerRawId: number, role: ManualRole) => void
  /** 改判请求进行中，菜单项禁用 */
  changingRole?: boolean
}

const ROLE_OPTIONS: { role: ManualRole; label: string }[] = [
  { role: 'interviewer', label: '设为面试官' },
  { role: 'candidate', label: '设为候选人' },
  { role: 'auto', label: '恢复自动判定' },
]

export function TranscriptView({
  speakers,
  segments,
  onChangeRole,
  changingRole = false,
}: TranscriptViewProps) {
  // 当前打开菜单的说话人编号
  const [menuFor, setMenuFor] = useState<number | null>(null)

  const speakerMap = useMemo(() => {
    const map = new Map<number, Speaker>()
    for (const s of speakers) map.set(s.speaker_raw_id, s)
    return map
  }, [speakers])

  if (segments.length === 0) {
    return (
      <p className="border border-dashed border-rule-strong bg-paper-raised px-4 py-8 text-center text-sm text-ink-faint">
        还没有转写文本。
      </p>
    )
  }

  const canEdit = onChangeRole != null

  return (
    <div className="space-y-4">
      {/* 说话人图例。可改判时徽章是按钮：点击弹菜单选择角色。 */}
      <div className="flex flex-wrap items-center gap-2">
        {speakers.map((s) => {
          const menuOpen = menuFor === s.speaker_raw_id
          return (
            <Fragment key={s.speaker_raw_id}>
              <button
                type="button"
                disabled={!canEdit || changingRole}
                onClick={() => setMenuFor(menuOpen ? null : s.speaker_raw_id)}
                title={
                  canEdit
                    ? '点击改判该说话人的角色（改判后会自动重跑切分与评分）'
                    : undefined
                }
                className={`inline-flex items-center gap-2 rounded-sm border bg-paper-raised px-2 py-1 transition-colors ${
                  canEdit
                    ? 'cursor-pointer border-rule hover:border-rule-strong hover:bg-paper-sunken disabled:cursor-wait disabled:opacity-60'
                    : 'cursor-default border-rule'
                }`}
              >
                <Badge tone={roleTone(s.role) as BadgeTone}>{roleLabel(s.role)}</Badge>
                <span className="mono text-[11px] text-ink-faint">
                  {s.speaker_raw_id}
                </span>
                <span className="text-[11px] text-ink-faint tnum">
                  置信度 {(s.confidence * 100).toFixed(0)}%
                </span>
                {s.manual_override && <Badge tone="amber">已手动指定</Badge>}
                {canEdit && (
                  <svg
                    viewBox="0 0 20 20"
                    fill="currentColor"
                    className="size-3 text-ink-faint"
                    aria-hidden
                  >
                    <path
                      fillRule="evenodd"
                      d="M5.2 7.2a.9.9 0 0 1 1.27 0L10 10.73l3.53-3.53a.9.9 0 1 1 1.27 1.27l-4.16 4.17a.9.9 0 0 1-1.27 0L5.2 8.47a.9.9 0 0 1 0-1.27z"
                      clipRule="evenodd"
                    />
                  </svg>
                )}
              </button>

              {menuOpen && (
                <>
                  {/* 透明背板：点菜单外面任意位置关闭 */}
                  <button
                    type="button"
                    aria-label="关闭菜单"
                    className="fixed inset-0 z-40 cursor-default"
                    onClick={() => setMenuFor(null)}
                  />
                  <div className="relative z-50 -mt-1">
                    <div className="absolute left-0 top-1 w-44 border border-rule-strong bg-paper-raised shadow-lg">
                      <p className="label-cap border-b border-rule px-3 py-1.5">
                        说话人 {s.speaker_raw_id}
                      </p>
                      {ROLE_OPTIONS.map((opt) => (
                        <button
                          key={opt.role}
                          type="button"
                          disabled={changingRole}
                          onClick={() => {
                            setMenuFor(null)
                            onChangeRole?.(s.speaker_raw_id, opt.role)
                          }}
                          className={`flex w-full items-center justify-between px-3 py-2 text-left text-sm transition-colors hover:bg-paper-sunken disabled:opacity-50 ${
                            opt.role === 'auto' ? 'border-t border-rule text-ink-faint' : 'text-ink'
                          }`}
                        >
                          {opt.label}
                          {/* 当前角色打勾。"auto" 不是存储值，只对人工改判过的显示对勾 */}
                          {(s.role === opt.role ||
                            (opt.role === 'auto' && !s.manual_override)) && (
                            <span className="text-seal">✓</span>
                          )}
                        </button>
                      ))}
                    </div>
                  </div>
                </>
              )}
            </Fragment>
          )
        })}
        {canEdit && (
          <span className="text-[11px] text-ink-faint">
            点击标签可人工改判；改判后自动重跑切分、评分与汇总
          </span>
        )}
      </div>

      <ul className="divide-y divide-rule border border-rule bg-paper-raised">
        {segments.map((seg) => {
          const speaker = speakerMap.get(seg.speaker_raw_id)
          const role = speaker?.role ?? 'unknown'
          const corrected =
            seg.corrected_text != null &&
            seg.corrected_text.trim() !== '' &&
            seg.corrected_text !== seg.text
          const tone = roleTone(role) as BadgeTone
          return (
            <li key={seg.seq} className="flex gap-3 px-4 py-2.5">
              <span className="mono tnum mt-0.5 w-12 shrink-0 text-[11px] text-ink-faint">
                {formatTimestamp(seg.start_ms)}
              </span>
              <span className="mt-0.5 w-16 shrink-0">
                <Badge tone={tone}>{roleLabel(role)}</Badge>
              </span>
              <div className="min-w-0 flex-1">
                {corrected ? (
                  <p className="bg-warn-soft px-2 py-1 text-sm text-ink ring-1 ring-warn/20 ring-inset">
                    {seg.corrected_text}
                    <span className="mono mt-1 block text-xs text-ink-faint line-through">
                      {seg.text}
                    </span>
                  </p>
                ) : (
                  <p className="text-sm text-ink-soft">{seg.text}</p>
                )}
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

export function TranscriptLegendNote() {
  return (
    <p className="text-xs text-ink-faint">
      高亮句子表示该句经过纠错，删除线为原始识别文本。
    </p>
  )
}
