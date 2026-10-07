import type {
  CostResponse,
  CreateInterviewResponse,
  CreateRunResponse,
  HealthResponse,
  InterviewDetail,
  InterviewListItem,
  ReportResponse,
  Run,
  SpeakerMappingResponse,
  SpeakerMappingUpdateItem,
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

  get isUnauthorized(): boolean {
    return this.status === 401
  }
}

const TOKEN_KEY = 'interview-review-access-token'

export function getAccessToken(): string {
  return localStorage.getItem(TOKEN_KEY) ?? ''
}

export function setAccessToken(code: string): void {
  if (code) localStorage.setItem(TOKEN_KEY, code)
  else localStorage.removeItem(TOKEN_KEY)
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
  const headers = new Headers(init?.headers)
  const token = getAccessToken()
  if (token) headers.set('X-Access-Token', token)

  let res: Response
  try {
    res = await fetch(`/api${path}`, { ...init, headers })
  } catch {
    throw new ApiError(0, '无法连接到后端服务，请确认后端已启动。')
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

    // 口令缺失或错误：通知全局的 AccessGate 弹出输入框。
    // 排除 /health —— 页脚的健康指示不该触发弹框。
    if (res.status === 401 && !path.startsWith('/health')) {
      window.dispatchEvent(new CustomEvent('auth:required'))
    }

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

  updateSpeakerMapping(
    id: string,
    mappings: SpeakerMappingUpdateItem[],
  ): Promise<SpeakerMappingResponse> {
    return request<SpeakerMappingResponse>(
      `/interviews/${encodeURIComponent(id)}/speaker-mapping`,
      {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mappings }),
      },
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
