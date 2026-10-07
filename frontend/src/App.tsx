import { Link, Route, Routes } from 'react-router-dom'
import { AccessGate } from './components/AccessGate'
import { Layout } from './components/Layout'
import { ErrorBoundary } from './components/ErrorBoundary'
import { InterviewListPage } from './pages/InterviewListPage'
import { InterviewDetailPage } from './pages/InterviewDetailPage'
import { ReportPage } from './pages/ReportPage'

function NotFoundPage() {
  return (
    <div className="panel flex flex-col items-center justify-center py-24 text-center">
      <p className="display text-5xl text-ink-faint tnum">404</p>
      <p className="mt-3 text-sm font-medium text-ink-soft">页面不存在</p>
      <Link
        to="/"
        className="mt-4 rounded-sm bg-ink px-3 py-1.5 text-sm font-medium text-paper-raised hover:bg-ink-soft"
      >
        返回面试列表
      </Link>
    </div>
  )
}

function App() {
  return (
    <ErrorBoundary>
      <AccessGate />
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<InterviewListPage />} />
          <Route path="interviews/:id" element={<InterviewDetailPage />} />
          <Route path="interviews/:id/report" element={<ReportPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Route>
      </Routes>
    </ErrorBoundary>
  )
}

export default App
