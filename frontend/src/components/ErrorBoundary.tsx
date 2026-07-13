import { Component, type ErrorInfo, type ReactNode } from 'react'
import { Link } from 'react-router-dom'

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
}

/**
 * #172（裸的錯誤頁 / 無首頁連結）：路由內容的 runtime 錯誤兜底。
 *
 * 沒有這層時，任一被渲染的元件在 render/lifecycle 拋錯（例如畸形 API 資料
 * 進到圖表元件），React 會把整棵樹 unmount 成一片空白畫面，評審現場操作
 * demo 撞到就無路可回——這正是「裸的錯誤頁 + 無首頁連結」要防的失敗模式。
 *
 * 刻意包在 `<Header />` 之外的 `<Routes>` 外層（見 App.tsx）：Header 仍在，
 * 主導覽與 logo 回首頁連結永遠可用；本 fallback 另外再給一個明確的回首頁鈕。
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false }

  static getDerivedStateFromError(): State {
    return { hasError: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // 觀測性：留在 console 供部署後排查（不吐 stack 到畫面，避免裸露技術細節）。
    console.error('[ErrorBoundary] 未預期的畫面錯誤：', error, info.componentStack)
  }

  private handleReset = () => {
    this.setState({ hasError: false })
  }

  render() {
    if (!this.state.hasError) return this.props.children

    return (
      <main
        className="mx-auto flex max-w-xl flex-col items-center gap-3 px-4 py-16 text-center"
        role="alert"
      >
        <h1 className="text-2xl font-bold text-tf-text">頁面發生未預期的錯誤</h1>
        <p className="text-sm text-tf-muted">
          這個畫面遇到問題無法顯示。你可以重試，或回到首頁重新開始。
        </p>
        <div className="mt-2 flex items-center gap-3">
          <Link
            to="/"
            onClick={this.handleReset}
            className="rounded-md bg-tf-accent px-3 py-2 text-sm font-semibold text-white no-underline transition hover:opacity-90"
          >
            回首頁
          </Link>
          <button
            type="button"
            onClick={this.handleReset}
            className="rounded-md border border-tf-border px-3 py-2 text-sm font-semibold text-tf-text transition hover:border-tf-accent"
          >
            重試
          </button>
        </div>
      </main>
    )
  }
}
