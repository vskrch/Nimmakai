import { useState, useCallback } from 'react'
import Sidebar from './components/Sidebar'
import AuthModal from './components/AuthModal'
import { Toast } from './components/ui'
import { useAuth, useSSE } from './hooks/useApi'
import { ap } from './lib/api'

import DashboardPage from './pages/DashboardPage'
import AnalyticsOverviewPage from './pages/AnalyticsOverviewPage'
import RequestsPage from './pages/RequestsPage'
import LiveFeedPage from './pages/LiveFeedPage'
import IntentsPage from './pages/IntentsPage'
import CostPage from './pages/CostPage'
import ProvidersPage from './pages/ProvidersPage'
import HealthPage from './pages/HealthPage'
import ModelsPage from './pages/ModelsPage'
import RoutingPage from './pages/RoutingPage'
import PlaygroundPage from './pages/PlaygroundPage'

const PAGE_TITLES: Record<string, string> = {
  dashboard: 'Overview',
  analytics: 'Analytics',
  requests: 'Request Explorer',
  live: 'Live Feed',
  intents: 'Intent Analytics',
  cost: 'Cost Center',
  playground: 'Playground',
  providers: 'Providers',
  health: 'Provider Health',
  models: 'Models',
  routing: 'Routing',
}

export default function App() {
  const { authed, showAuth, setShowAuth, doAuth } = useAuth()
  const [page, setPage] = useState('dashboard')
  const [toast, setToast] = useState<{ msg: string; type: 'ok' | 'err' } | null>(null)
  const sse = useSSE()

  function showToast(msg: string, type: 'ok' | 'err' = 'ok') {
    setToast({ msg, type })
  }

  async function handleRefreshAll() {
    showToast('Refreshing catalog + rankings...')
    const r = await ap('/admin/catalog/refresh', {})
    if (r && (r as Record<string, unknown>).ok !== false) {
      showToast('Catalog refreshed')
    } else {
      showToast('Refresh completed with warnings', 'err')
    }
  }

  const handlePageChange = useCallback((p: string) => setPage(p), [])

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar page={page} onNavigate={handlePageChange} />

      <div className="flex-1 flex flex-col bg-[#09090b] relative min-w-0">
        <div className="h-16 border-b border-white/[0.08] flex items-center px-8 justify-between backdrop-blur-xl bg-[#09090b]/70 z-10 shrink-0">
          <h2 className="text-[15px] font-semibold">{PAGE_TITLES[page] || page}</h2>
          <div className="flex items-center gap-3 text-xs bg-white/[0.03] px-3 py-1.5 rounded-full border border-white/[0.08]">
            <span
              className={`w-1.5 h-1.5 rounded-full ${sse ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.6)]' : 'bg-zinc-500'}`}
            />
            {sse
              ? `${sse.live_models} models · ${sse.active_providers} providers`
              : authed ? 'Connecting...' : 'Not connected'
            }
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-8">
          {page === 'dashboard' && <DashboardPage onRefresh={handleRefreshAll} />}
          {page === 'analytics' && <AnalyticsOverviewPage />}
          {page === 'requests' && <RequestsPage />}
          {page === 'live' && <LiveFeedPage />}
          {page === 'intents' && <IntentsPage />}
          {page === 'cost' && <CostPage />}
          {page === 'providers' && <ProvidersPage />}
          {page === 'health' && <HealthPage />}
          {page === 'models' && <ModelsPage />}
          {page === 'routing' && <RoutingPage />}
          {page === 'playground' && <PlaygroundPage />}
        </div>
      </div>

      {showAuth && !authed && (
        <AuthModal onAuth={doAuth} />
      )}

      {toast && (
        <Toast message={toast.msg} type={toast.type} onDismiss={() => setToast(null)} />
      )}
    </div>
  )
}
