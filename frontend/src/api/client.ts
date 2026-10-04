import type {
  CostResponse,
  CreateInterviewResponse,
  CreateRunResponse,
  HealthResponse,
  InterviewDetail,
  InterviewListItem,
  ReportResponse,
  Run,
  TranscriptResponse,
} from './types'

/** 统一的 API 错误。status 为 0 表示网络层失败（后端没起来等）。 */
export class ApiError extends Error {
  status: number
  payload: unknown

  constructor(status: number, message: string, payload?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.payload = payload
  }

  get isNotFound(): boolean {
    return this.status === 404
  }

  get isNetwork(): boolean {
    return this.status === 0
  }
}

function extractDetail(data: unknown): string | null {
  if (data && typeof data === 'object') {
    const obj = data as Record<string, unknown>
    const candidate = obj.detail ?? obj.message ?? obj.error
    if (typeof candidate === 'string' && candidate.trim()) return candidate
  }
  if (typeof data === 'string' && data.trim()) return data
  return null
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`/api${path}`, init)
  } catch {
    throw new ApiError(0, '无法连接到后端服务，请确认后端已在 127.0.0.1:8000 启动。')
  }

  if (!res.ok) {
    let payload: unknown = null
    try {
      const text = await res.text()
      if (text) {
        try {
          payload = JSON.parse(text)
        } catch {
          payload = text
        }
      }
    } catch {
      // ignore body parse errors
    }
    const detail = extractDetail(payload)
    throw new ApiError(res.status, detail ?? `请求失败（HTTP ${res.status}）`, payload)
  }

  if (res.status === 204) {
    return undefined as T
  }

  const contentType = res.headers.get('content-type') ?? ''
  if (contentType.includes('application/json')) {
    return (await res.json()) as T
  }
  return (await res.text()) as unknown as T
}

export const api = {
  listInterviews(): Promise<InterviewListItem[]> {
    return request<InterviewListItem[]>('/interviews')
  },

  createInterview(form: FormData): Promise<CreateInterviewResponse> {
    return request<CreateInterviewResponse>('/interviews', {
      method: 'POST',
      body: form,
    })
  },

  deleteInterview(id: string): Promise<void> {
    return request<void>(`/interviews/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    })
  },

  getInterview(id: string): Promise<InterviewDetail> {
    return request<InterviewDetail>(`/interviews/${encodeURIComponent(id)}`)
  },

  createRun(id: string, fromStage?: string): Promise<CreateRunResponse> {
    return request<CreateRunResponse>(
      `/interviews/${encodeURIComponent(id)}/runs`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(fromStage ? { from_stage: fromStage } : {}),
      },
    )
  },

  getRun(runId: string): Promise<Run> {
    return request<Run>(`/runs/${encodeURIComponent(runId)}`)
  },

  getTranscript(id: string): Promise<TranscriptResponse> {
    return request<TranscriptResponse>(
      `/interviews/${encodeURIComponent(id)}/transcript`,
    )
  },

  getReport(id: string): Promise<ReportResponse> {
    return request<ReportResponse>(
      `/interviews/${encodeURIComponent(id)}/report`,
    )
  },

  getCost(id: string): Promise<CostResponse> {
    return request<CostResponse>(`/interviews/${encodeURIComponent(id)}/cost`)
  },

  getHealth(): Promise<HealthResponse> {
    return request<HealthResponse>('/health')
  },
}
