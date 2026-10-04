import { useMemo } from 'react'
import type { Segment, Speaker } from '../api/types'
import { Badge } from './Badge'
import type { BadgeTone } from './Badge'
import { formatTimestamp } from '../lib/format'
import { roleLabel, roleTone } from '../lib/status'

interface TranscriptViewProps {
  speakers: Speaker[]
  segments: Segment[]
}

export function TranscriptView({ speakers, segments }: TranscriptViewProps) {
  const speakerMap = useMemo(() => {
    const map = new Map<string, Speaker>()
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

  return (
    <div className="space-y-4">
      {/* 说话人图例 */}
      <div className="flex flex-wrap items-center gap-2">
        {speakers.map((s) => (
          <span
            key={s.speaker_raw_id}
            className="inline-flex items-center gap-2 rounded-sm border border-rule bg-paper-raised px-2 py-1"
          >
            <Badge tone={roleTone(s.role) as BadgeTone}>{roleLabel(s.role)}</Badge>
            <span className="mono text-[11px] text-ink-faint">
              {s.speaker_raw_id}
            </span>
            <span className="text-[11px] text-ink-faint tnum">
              置信度 {(s.confidence * 100).toFixed(0)}%
            </span>
            {s.manual_override && <Badge tone="amber">已手动指定</Badge>}
          </span>
        ))}
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
