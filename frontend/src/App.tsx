import { Link, Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { ErrorBoundary } from './components/ErrorBoundary'
import { InterviewListPage } from './pages/InterviewListPage'
import { InterviewDetailPage } from './pages/InterviewDetailPage'
import { ReportPage } from './pages/ReportPage'

function NotFoundPage() {
  return (
    <div className="flex flex-col items-center justify-center rounded-xl border border-neutral-200 bg-white py-24 text-center">
      <p className="text-4xl font-semibold text-neutral-300">404</p>
      <p className="mt-3 text-sm font-medium text-neutral-700">页面不存在</p>
      <Link
        to="/"
        className="mt-4 rounded-md bg-neutral-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-neutral-800"
      >
        返回面试列表
      </Link>
    </div>
  )
}

function App() {
  return (
    <ErrorBoundary>
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
