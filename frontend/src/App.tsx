import { useState, useCallback } from 'react'
import Sidebar from './components/Sidebar'
import AuthModal, { type AuthSession } from './components/AuthModal'
import { Toast } from './components/ui'
import { useAuth, useSSE } from './hooks/useApi'
import { api, ap } from './lib/api'

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
import UsersPage from './pages/UsersPage'
import AccountPage from './pages/AccountPage'

const PAGE_TITLES: Record<string, string> = {
  dashboard: 'Overview',
  analytics: 'Analytics',
  requests: 'Request Explorer',
  live: 'Live Feed',
  intents: 'Intent Analytics',
  cost: 'Cost Center',
  playground: 'Playground',
  account: 'Account',
  users: 'Users',
  providers: 'Providers',
  health: 'Provider Health',
  models: 'Models',
  routing: 'Routing',
}

export default function App() {
  const {
    ready, authed, showAuth, session, applySession, logout, isAdmin, status, email,
  } = useAuth()
  const [page, setPage] = useState('dashboard')
  const [toast, setToast] = useState<{ msg: string; type: 'ok' | 'err' } | null>(null)
  const sse = useSSE()

  function showToast(msg: string, type: 'ok' | 'err' = 'ok') {
    setToast({ msg, type })
  }

  async function handleRefreshAll() {
    if (!isAdmin) return
    showToast('Refreshing catalog + rankings...')
    const r = await ap('/admin/catalog/refresh', {})
    if (r && (r as Record<string, unknown>).ok !== false) {
      showToast('Catalog refreshed')
    } else {
      showToast('Refresh completed with warnings', 'err')
    }
  }

  const handlePageChange = useCallback((p: string) => setPage(p), [])

  const refreshSession = useCallback(async () => {
    const me = await api<AuthSession>('/auth/me')
    if (me?.authenticated) applySession(me)
  }, [applySession])

  const pending = status === 'pending_approval' || status === 'unverified'

  if (!ready) {
    return (
      <div className="h-screen flex items-center justify-center bg-[#09090b] text-zinc-500 text-sm">
        Loading…
      </div>
    )
  }

  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar
        page={page}
        onNavigate={handlePageChange}
        isAdmin={isAdmin}
        email={email}
        onLogout={logout}
      />

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

        {pending && (
          <div className="mx-8 mt-4 rounded-xl border border-amber-500/30 bg-amber-500/10 px-4 py-3 text-sm text-amber-100">
            {status === 'unverified'
              ? 'Verify your email to continue. After that an admin must approve your account.'
              : 'Your account is pending admin approval. An API key will be issued when approved.'}
          </div>
        )}

        <div className="flex-1 overflow-y-auto p-8">
          {page === 'dashboard' && <DashboardPage onRefresh={handleRefreshAll} />}
          {page === 'analytics' && <AnalyticsOverviewPage />}
          {page === 'requests' && <RequestsPage />}
          {page === 'live' && <LiveFeedPage />}
          {page === 'intents' && <IntentsPage />}
          {page === 'cost' && <CostPage />}
          {page === 'account' && <AccountPage session={session} onRefresh={refreshSession} />}
          {page === 'users' && isAdmin && <UsersPage />}
          {page === 'providers' && isAdmin && <ProvidersPage />}
          {page === 'health' && isAdmin && <HealthPage />}
          {page === 'models' && isAdmin && <ModelsPage />}
          {page === 'routing' && isAdmin && <RoutingPage />}
          {page === 'playground' && <PlaygroundPage />}
        </div>
      </div>

      {showAuth && !authed && (
        <AuthModal onSession={applySession} />
      )}

      {toast && (
        <Toast message={toast.msg} type={toast.type} onDismiss={() => setToast(null)} />
      )}
    </div>
  )
}
