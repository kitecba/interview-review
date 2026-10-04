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
      <p className="rounded-md border border-dashed border-neutral-300 bg-white px-4 py-8 text-center text-sm text-neutral-500">
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
            className="inline-flex items-center gap-2 rounded-md border border-neutral-200 bg-white px-2 py-1"
          >
            <Badge tone={roleTone(s.role) as BadgeTone}>{roleLabel(s.role)}</Badge>
            <span className="font-mono text-[11px] text-neutral-400">
              {s.speaker_raw_id}
            </span>
            <span className="text-[11px] text-neutral-400">
              置信度 {(s.confidence * 100).toFixed(0)}%
            </span>
            {s.manual_override && <Badge tone="amber">已手动指定</Badge>}
          </span>
        ))}
      </div>

      <ul className="divide-y divide-neutral-100 overflow-hidden rounded-lg border border-neutral-200 bg-white">
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
              <span className="mt-0.5 w-12 shrink-0 font-mono text-[11px] text-neutral-400 tabular-nums">
                {formatTimestamp(seg.start_ms)}
              </span>
              <span className="mt-0.5 w-16 shrink-0">
                <Badge tone={tone}>{roleLabel(role)}</Badge>
              </span>
              <div className="min-w-0 flex-1">
                {corrected ? (
                  <p className="rounded bg-amber-50 px-2 py-1 text-sm text-neutral-800 ring-1 ring-amber-100 ring-inset">
                    {seg.corrected_text}
                    <span className="mt-1 block text-xs text-neutral-400 line-through">
                      {seg.text}
                    </span>
                  </p>
                ) : (
                  <p className="text-sm text-neutral-700">{seg.text}</p>
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
    <p className="text-xs text-neutral-400">
      高亮句子表示该句经过纠错，删除线为原始识别文本。
    </p>
  )
}
