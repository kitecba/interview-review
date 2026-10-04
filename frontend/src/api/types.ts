// 后端 API 契约对应的类型定义。字段名与后端严格保持一致。

export type InterviewStatus =
  | 'created'
  | 'processing'
  | 'done'
  | 'failed'
  | 'canceled'

export type StageStatus = 'pending' | 'running' | 'done' | 'failed' | 'skipped'

export type RunStatus =
  | 'pending'
  | 'running'
  | 'done'
  | 'failed'
  | 'canceled'

export type SpeakerRole = 'interviewer' | 'candidate' | 'unknown'

export type Confidence = 'high' | 'medium' | 'low'

/** GET /api/interviews 的单条记录 */
export interface InterviewListItem {
  id: string
  title: string
  company: string | null
  position: string | null
  status: InterviewStatus
  created_at: string
  overall_score: number | null
  report_version: number | null
}

/** GET /api/interviews/{id} 内的 interview 字段 */
export interface Interview {
  id: string
  title: string
  company: string | null
  position: string | null
  status: InterviewStatus
  created_at: string
}

export interface AudioMeta {
  original_filename: string
  duration_ms: number
  size_bytes: number
}

export interface Run {
  id: string
  status: RunStatus
  progress: number
  message: string | null
  current_stage: string | null
}

export interface StageInfo {
  stage: string
  label: string
  status: StageStatus
  attempt: number
  error: string | null
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
}

export interface InterviewDetail {
  interview: Interview
  audio: AudioMeta | null
  run: Run | null
  stages: StageInfo[]
}

export interface Speaker {
  speaker_raw_id: string
  role: SpeakerRole
  confidence: number
  evidence: string | null
  manual_override: boolean
}

export interface Segment {
  seq: number
  speaker_raw_id: string
  start_ms: number
  end_ms: number
  text: string
  corrected_text: string | null
}

export interface TranscriptResponse {
  speakers: Speaker[]
  segments: Segment[]
}

// ---- 报告 ----

export interface ImprovementPlanItem {
  area: string
  actions: string[]
}

export interface PredictedResult {
  verdict: string
  confidence: Confidence
  reason: string
}

export interface ReportSummary {
  overall_score: number
  assessment: string
  competency_radar: Record<string, number>
  top_strengths: string[]
  critical_weaknesses: string[]
  recurring_patterns: string[]
  improvement_plan: ImprovementPlanItem[]
  predicted_result: PredictedResult
}

export interface Report {
  version: number
  overall_score: number
  model: string | null
  summary: ReportSummary
}

export interface KnowledgePoint {
  title: string
  detail: string
}

export interface QAAnalysis {
  overall_score: number
  summary: string
  dimension_scores: Record<string, number>
  strengths: string[]
  weaknesses: string[]
  improvement: string[]
  knowledge_points: KnowledgePoint[]
  predicted_followups: string[]
}

export interface QAItem {
  seq: number
  topic: string | null
  question_text: string
  answer_text: string
  is_followup: boolean
  is_off_topic: boolean
  analysis: QAAnalysis | null
}

export interface ReportResponse {
  report: Report | null
  qa: QAItem[]
}

// ---- 成本 ----

export interface CostItem {
  stage: string
  calls: number
  prompt_tokens: number
  completion_tokens: number
  cost: number
}

export interface CostResponse {
  total_cost: number
  total_cost_cny: number
  items: CostItem[]
}

export interface HealthResponse {
  ready: boolean
  checks: Record<string, unknown>
}

export interface CreateInterviewResponse {
  interview_id: string
  run_id: string
}

export interface CreateRunResponse {
  run_id: string
}
