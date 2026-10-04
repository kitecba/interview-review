import { useCallback, useEffect, useRef, useState } from 'react'
import type { DependencyList, Dispatch, SetStateAction } from 'react'
import { ApiError } from '../api/client'

export interface ApiResource<T> {
  data: T | null
  error: ApiError | null
  loading: boolean
  /** 手动重新拉取。 */
  reload: () => void
  /** 本地更新数据（用于轮询时原地合并）。 */
  setData: Dispatch<SetStateAction<T | null>>
}

/**
 * 通用数据拉取 hook。deps 变化或调用 reload 时重新拉取。
 * 组件卸载后不会 setState。
 */
export function useApi<T>(
  fetcher: () => Promise<T>,
  deps: DependencyList = [],
): ApiResource<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<ApiError | null>(null)
  const [loading, setLoading] = useState(true)
  const [nonce, setNonce] = useState(0)

  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  const reload = useCallback(() => setNonce((n) => n + 1), [])

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    fetcherRef
      .current()
      .then((result) => {
        if (cancelled) return
        setData(result)
        setError(null)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setError(
          err instanceof ApiError ? err : new ApiError(0, String(err)),
        )
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce])

  return { data, error, loading, reload, setData }
}

/**
 * 在 shouldPoll 为真时按 interval 周期调用回调（通常传 reload）。
 */
export function usePolling(
  shouldPoll: boolean,
  callback: () => void,
  interval = 2500,
) {
  const callbackRef = useRef(callback)
  callbackRef.current = callback
  useEffect(() => {
    if (!shouldPoll) return
    const timer = window.setInterval(() => callbackRef.current(), interval)
    return () => window.clearInterval(timer)
  }, [shouldPoll, interval])
}
