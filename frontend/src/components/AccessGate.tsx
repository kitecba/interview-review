import { useEffect, useState } from 'react'
import { getAccessToken, setAccessToken } from '../api/client'

/**
 * 访问口令输入框（部署模式）。
 *
 * 后端设置了 APP_ACCESS_CODE 时，任何 API 调用返回 401 都会广播
 * `auth:required` 事件，这里监听到就弹出全屏输入框。提交后写入
 * localStorage 并整页刷新 —— 所有请求会自动带上新口令。
 *
 * 本地开发（后端未设口令）不会有 401，这个组件永远不出现。
 */
export function AccessGate() {
  const [needed, setNeeded] = useState(false)
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const onAuthRequired = () => setNeeded(true)
    window.addEventListener('auth:required', onAuthRequired)
    return () => window.removeEventListener('auth:required', onAuthRequired)
  }, [])

  if (!needed) return null

  function submit() {
    const trimmed = code.trim()
    if (!trimmed) {
      setError('请输入访问口令')
      return
    }
    // 先试探一次健康检查之外的接口没有意义 —— 直接存起来刷新，
    // 口令错误的话下一个请求又会 401，输入框会再次出现
    setAccessToken(trimmed)
    window.location.reload()
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink/80 p-4">
      <div className="panel w-full max-w-sm p-6">
        <h2 className="text-lg font-semibold text-ink">需要访问口令</h2>
        <p className="mt-2 text-sm leading-relaxed text-ink-soft">
          这份服务开启了访问控制，请向管理员索取口令。
          {getAccessToken() && ' 当前口令无效，请重新输入。'}
        </p>
        <input
          type="password"
          autoFocus
          value={code}
          onChange={(e) => {
            setCode(e.target.value)
            setError(null)
          }}
          onKeyDown={(e) => e.key === 'Enter' && submit()}
          placeholder="访问口令"
          className="mt-4 w-full border border-rule-strong bg-paper px-3 py-2 text-sm text-ink outline-none focus:border-ink-soft"
        />
        {error && <p className="mt-2 text-xs text-bad">{error}</p>}
        <button
          type="button"
          onClick={submit}
          className="mt-4 w-full rounded-sm bg-ink px-4 py-2 text-sm font-medium text-paper-raised transition-colors hover:bg-ink-soft"
        >
          进入
        </button>
      </div>
    </div>
  )
}
