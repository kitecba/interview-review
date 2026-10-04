/** 时间戳显示成 mm:ss（相对录音开头）。 */
export function formatTimestamp(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return '--:--'
  const totalSeconds = Math.floor(ms / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`
}

/** 阶段耗时：短时用秒（保留一位小数），超过一分钟用 m:ss。 */
export function formatDuration(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return '—'
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  const totalSeconds = Math.round(ms / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}m${String(seconds).padStart(2, '0')}s`
}

/** 创建时间显示成 YYYY-MM-DD HH:mm。 */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

/** 相对时间：刚刚 / N 分钟前 / N 小时前 / N 天前。 */
export function formatRelative(iso: string | null | undefined): string {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const diff = Date.now() - date.getTime()
  if (diff < 0) return formatDateTime(iso)
  const minute = 60_000
  const hour = 60 * minute
  const day = 24 * hour
  if (diff < minute) return '刚刚'
  if (diff < hour) return `${Math.floor(diff / minute)} 分钟前`
  if (diff < day) return `${Math.floor(diff / hour)} 小时前`
  if (diff < 30 * day) return `${Math.floor(diff / day)} 天前`
  return formatDateTime(iso)
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes) || bytes < 0) return '—'
  const units = ['B', 'KB', 'MB', 'GB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value >= 100 || unit === 0 ? Math.round(value) : value.toFixed(1)} ${units[unit]}`
}

/** 分数字符串：可能有小数。 */
export function formatScore(score: number | null | undefined): string {
  if (score == null || !Number.isFinite(score)) return '—'
  return Number.isInteger(score) ? String(score) : score.toFixed(1)
}

export function formatPercent(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '0%'
  return `${Math.round(value)}%`
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}
