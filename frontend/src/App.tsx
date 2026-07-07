import { BrowserRouter, Route, Routes } from 'react-router-dom'
import Header from './components/Header'
import HomePage from './pages/HomePage'
import AnalyzePage from './pages/AnalyzePage'
import ComparePage from './pages/ComparePage'
import StatusPage from './pages/StatusPage'
import CostsPage from './pages/CostsPage'
import HistoryPage from './pages/HistoryPage'
import AdminPage from './pages/AdminPage'
import NotFoundPage from './pages/NotFoundPage'

export default function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-tf-bg">
        <Header />
        <Routes>
          <Route path="/" element={<HomePage />} />
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
      </div>
    </BrowserRouter>
  )
}
