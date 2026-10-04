import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
}

/** 兜底错误边界，避免渲染异常导致整页白屏。 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('UI 渲染异常：', error, info.componentStack)
  }

  handleReset = () => {
    this.setState({ error: null })
  }

  render() {
    if (this.state.error) {
      return (
        <div className="mx-auto flex min-h-screen max-w-2xl flex-col items-center justify-center px-6 text-center">
          <h1 className="text-lg font-semibold text-ink">页面出错了</h1>
          <p className="mt-2 text-sm text-ink-soft">
            渲染过程中发生异常，可以尝试重新加载。
          </p>
          <pre className="mt-4 max-h-48 w-full overflow-auto bg-paper-sunken p-3 text-left text-xs text-ink-soft">
            {this.state.error.message}
          </pre>
          <div className="mt-5 flex gap-3">
            <button
              type="button"
              onClick={this.handleReset}
              className="rounded-sm border border-rule-strong bg-paper-raised px-3 py-1.5 text-sm font-medium text-ink-soft hover:bg-paper-sunken"
            >
              重试渲染
            </button>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="rounded-sm bg-ink px-3 py-1.5 text-sm font-medium text-paper-raised hover:bg-ink-soft"
            >
              重新加载
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
