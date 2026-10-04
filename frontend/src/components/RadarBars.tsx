import { clamp } from '../lib/format'
import { scoreBarColor } from '../lib/status'

interface RadarBarsProps {
  /** 能力名 → 0-100 的分数 */
  data: Record<string, number>
}

export function RadarBars({ data }: RadarBarsProps) {
  const entries = Object.entries(data)
  if (entries.length === 0) {
    return <p className="text-sm text-ink-faint">暂无能力评分。</p>
  }
  return (
    <div className="space-y-3">
      {entries.map(([name, value], index) => {
        const pct = clamp(value, 0, 100)
        return (
          <div
            key={name}
            // 能力名是模型根据面试内容取的，长度不可控（"Text2SQL / 数据治理
            // 方案设计"这种十几字很常见）。列宽给足并允许折行，不要截断 ——
            // 被截成"技术选型论证能…"就读不出是什么意思了。
            className="grid grid-cols-[210px_minmax(0,1fr)_44px] items-center gap-3"
          >
            <span className="text-sm leading-snug text-ink-soft" title={name}>
              {name}
            </span>
            <span className="h-2 w-full overflow-hidden bg-paper-sunken">
              <span
                className={`bar-grow block h-full ${scoreBarColor(pct)}`}
                style={{
                  width: `${pct}%`,
                  animationDelay: `${index * 60}ms`,
                }}
              />
            </span>
            <span className="display text-right text-sm leading-none text-ink tnum">
              {Math.round(pct)}
            </span>
          </div>
        )
      })}
    </div>
  )
}
