import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import Header from './components/Header'
import ErrorBoundary from './components/ErrorBoundary'
import HermesDashboard from './pages/HermesDashboard'
import AnalyzePage from './pages/AnalyzePage'
import ComparePage from './pages/ComparePage'
import StatusPage from './pages/StatusPage'
import CostsPage from './pages/CostsPage'
import HistoryPage from './pages/HistoryPage'
import AdminPage from './pages/AdminPage'
import NotFoundPage from './pages/NotFoundPage'

/**
 * #172 codex 對抗審 HIGH 修正：ErrorBoundary 不會自動 reset，若不隨路由變化
 * 重掛，某頁拋錯進 fallback 後即使點 Header 導覽切換路由，`hasError` 仍為
 * true → 永遠卡 fallback、新頁不顯示（連「回首頁」都救不回）。
 * 以 `key={location.pathname}` 讓路由一變就重掛 boundary、自動清錯誤狀態；
 * 需在 `<BrowserRouter>` 內才能用 `useLocation`，故抽出本內層元件。
 */
function RoutedContent() {
  const location = useLocation()
  return (
    <ErrorBoundary key={location.pathname}>
      <Routes>
        {/* HERMES 整站 redesign 旗艦頁：取代舊首頁，自帶全屏 bridge 外殼。 */}
        <Route path="/" element={<HermesDashboard />} />
        {/* 舊首頁（HomePage）已由旗艦 / 取代，redirect 避免重複入口。 */}
        <Route path="/home" element={<Navigate to="/" replace />} />
        <Route path="/analyze" element={<AnalyzePage />} />
        <Route path="/compare" element={<ComparePage />} />
        <Route path="/status" element={<StatusPage />} />
        <Route path="/costs" element={<CostsPage />} />
        <Route path="/history" element={<HistoryPage />} />
        {/* 管理控制台（PR-4）：刻意不進 Header 主導覽（不對公開流量
            宣傳管理入口；真正的守門是後端 X-Admin-Token 認證+fail-closed，
            不是路徑隱蔽）。 */}
        <Route path="/admin" element={<AdminPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
    </ErrorBoundary>
  )
}

function Shell() {
  const location = useLocation()
  // HERMES 頁自帶全屏 bridge 頂欄，不再套用舊 Header。
  const showHeader = location.pathname !== '/'
  // hermes-surface 把全站沿用 `tf-*` token 的元件整批重對應到 HERMES 暗色
  // 視覺語言（見 hermes/hermes.css）；HERMES 首頁用 inline hermes-* 色，不受影響。
  return (
    <div className="hermes-surface min-h-screen bg-tf-bg">
      {showHeader && <Header />}
      <RoutedContent />
    </div>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Shell />
    </BrowserRouter>
  )
}
