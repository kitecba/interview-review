import type {
  InterviewStatus,
  RunStatus,
  StageInfo,
} from '../api/types'

export type Tone = 'neutral' | 'blue' | 'green' | 'red' | 'amber' | 'violet'

interface StatusMeta {
  label: string
  tone: Tone
}

/** 面试整体状态 */
const INTERVIEW_STATUS: Record<InterviewStatus, StatusMeta> = {
  created: { label: '待处理', tone: 'neutral' },
  processing: { label: '处理中', tone: 'blue' },
  done: { label: '已完成', tone: 'green' },
  failed: { label: '失败', tone: 'red' },
  canceled: { label: '已取消', tone: 'neutral' },
}

export function interviewStatusMeta(status: string): StatusMeta {
  return INTERVIEW_STATUS[status as InterviewStatus] ?? { label: status, tone: 'neutral' }
}

/** 运行状态 */
const RUN_STATUS: Record<RunStatus, StatusMeta> = {
  pending: { label: '排队中', tone: 'neutral' },
  running: { label: '运行中', tone: 'blue' },
  done: { label: '已完成', tone: 'green' },
  failed: { label: '失败', tone: 'red' },
  canceled: { label: '已取消', tone: 'neutral' },
}

export function runStatusMeta(status: string): StatusMeta {
  return RUN_STATUS[status as RunStatus] ?? { label: status, tone: 'neutral' }
}

/**
 * 阶段展示状态。
 *
 * 后端的 `skipped` 含义是「幂等命中、复用了上次的产物，这次没有真正执行」——
 * 这是用户关心的信息，因为它意味着这个阶段这次既没花时间也没花钱。
 * 所以显示成「复用缓存」而不是笼统的「已跳过」。
 */
export type StageDisplayStatus =
  | 'pending'
  | 'running'
  | 'done'
  | 'failed'
  | 'skipped'

const STAGE_STATUS: Record<StageDisplayStatus, StatusMeta> = {
  pending: { label: '等待', tone: 'neutral' },
  running: { label: '进行中', tone: 'blue' },
  done: { label: '完成', tone: 'green' },
  failed: { label: '失败', tone: 'red' },
  skipped: { label: '复用缓存', tone: 'neutral' },
}

export function stageDisplayStatus(stage: StageInfo): StageDisplayStatus {
  const raw = stage.status as string
  return raw in STAGE_STATUS ? (raw as StageDisplayStatus) : 'pending'
}

export function stageStatusMeta(stage: StageInfo): StatusMeta {
  return STAGE_STATUS[stageDisplayStatus(stage)]
}

/** 说话人角色 */
const ROLE_LABEL: Record<string, string> = {
  interviewer: '面试官',
  candidate: '候选人',
  unknown: '未知',
}

export function roleLabel(role: string): string {
  return ROLE_LABEL[role] ?? role
}

export function roleTone(role: string): Tone {
  if (role === 'interviewer') return 'violet'
  if (role === 'candidate') return 'blue'
  return 'neutral'
}

const CONFIDENCE_LABEL: Record<string, string> = {
  high: '高',
  medium: '中',
  low: '低',
}

export function confidenceLabel(confidence: string): string {
  return CONFIDENCE_LABEL[confidence] ?? confidence
}

export function confidenceTone(confidence: string): Tone {
  if (confidence === 'high') return 'green'
  if (confidence === 'medium') return 'amber'
  if (confidence === 'low') return 'red'
  return 'neutral'
}

/** 根据 0-100 的分数返回文字颜色（用于大号总分）。 */
export function scoreTextColor(score: number | null | undefined): string {
  if (score == null) return 'text-neutral-400'
  if (score >= 85) return 'text-emerald-600'
  if (score >= 70) return 'text-blue-600'
  if (score >= 55) return 'text-amber-600'
  return 'text-red-600'
}

/** 根据 0-100 的分数返回条形填充色。 */
export function scoreBarColor(score: number): string {
  if (score >= 85) return 'bg-emerald-500'
  if (score >= 70) return 'bg-blue-500'
  if (score >= 55) return 'bg-amber-500'
  return 'bg-red-500'
}
