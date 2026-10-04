import { Link, Outlet, useLocation } from 'react-router-dom'
import { api } from '../api/client'
import { useApi, usePolling } from '../hooks/useApi'

function HealthIndicator() {
  const { data, error, loading, reload } = useApi(() => api.getHealth(), [])
  usePolling(true, reload, 30_000)

  let tone = 'bg-neutral-300'
  let label = '检查后端…'
  if (!loading || data) {
    if (error) {
      tone = 'bg-red-500'
      label = '后端未连接'
    } else if (data?.ready) {
      tone = 'bg-emerald-500'
      label = '后端就绪'
    } else {
      tone = 'bg-amber-500'
      label = '后端未就绪'
    }
  }

  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-neutral-500">
      <span className={`size-1.5 rounded-full ${tone}`} />
      {label}
    </span>
  )
}

function Header() {
  const { pathname } = useLocation()
  const isList = pathname === '/'
  return (
    <header className="sticky top-0 z-20 border-b border-neutral-200 bg-white/85 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-6xl items-center gap-6 px-6">
        <Link to="/" className="flex items-center gap-2">
          <span className="flex size-6 items-center justify-center rounded-md bg-neutral-900 text-xs font-bold text-white">
            面
          </span>
          <span className="text-sm font-semibold tracking-tight text-neutral-900">
            面试复盘助手
          </span>
        </Link>
        <nav className="flex items-center gap-1 text-sm">
          <Link
            to="/"
            className={`rounded-md px-2.5 py-1.5 transition-colors ${
              isList
                ? 'bg-neutral-100 font-medium text-neutral-900'
                : 'text-neutral-500 hover:text-neutral-900'
            }`}
          >
            面试列表
          </Link>
        </nav>
        <div className="ml-auto">
          <HealthIndicator />
        </div>
      </div>
    </header>
  )
}

export function Layout() {
  return (
    <div className="flex min-h-screen min-w-[1024px] flex-col bg-neutral-50 text-neutral-900">
      <Header />
      <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-8">
        <Outlet />
      </main>
      <footer className="border-t border-neutral-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4 text-xs text-neutral-400">
          <span>面试复盘助手 · 本地工具</span>
          <HealthIndicator />
        </div>
      </footer>
    </div>
  )
}
