import { BrowserRouter, Route, Routes } from 'react-router-dom'
import Header from './components/Header'
import HomePage from './pages/HomePage'
import AnalyzePage from './pages/AnalyzePage'
import ComparePage from './pages/ComparePage'
import StatusPage from './pages/StatusPage'
import CostsPage from './pages/CostsPage'
import HistoryPage from './pages/HistoryPage'
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
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </div>
    </BrowserRouter>
  )
}
