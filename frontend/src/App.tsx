import React, { useState, useCallback } from 'react'
import Sidebar from './components/Sidebar'
import AuthModal, { type AuthSession } from './components/AuthModal'
import { Toast, Button } from './components/ui'
import { useAuth, useSSE } from './hooks/useApi'
import { api, ap } from './lib/api'
import { RefreshCw, Radio, ShieldAlert } from 'lucide-react'

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
import ModelLaddersPage from './pages/ModelLaddersPage'
import RLPage from './pages/RLPage'
import PlaygroundPage from './pages/PlaygroundPage'
import UsersPage from './pages/UsersPage'
import AccountPage from './pages/AccountPage'

const PAGE_META: Record<string, { title: string; subtitle: string }> = {
  dashboard: { title: 'System Overview', subtitle: 'Real-time gateway metrics & execution status' },
  analytics: { title: 'Analytics Center', subtitle: 'Throughput, latency, and operational telemetry' },
  requests: { title: 'Request Explorer', subtitle: 'Detailed request traces & execution payloads' },
  live: { title: 'Live Event Stream', subtitle: 'Real-time SSE event pipeline feed' },
  intents: { title: 'Intent Intelligence', subtitle: 'Intent classification breakdown & performance' },
  cost: { title: 'Cost & Savings', subtitle: 'Token expenditure & auto-router financial optimization' },
  playground: { title: 'API Playground', subtitle: 'Test model routing, streaming & capabilities' },
  account: { title: 'API Keys & Account', subtitle: 'Manage API tokens and access credentials' },
  users: { title: 'User Management', subtitle: 'Control platform permissions & approvals' },
  providers: { title: 'LLM Providers', subtitle: 'Manage provider API credentials & concurrency' },
  health: { title: 'Provider Health', subtitle: 'Latency telemetry, cooldowns & circuit breakers' },
  models: { title: 'Model Catalog', subtitle: 'Live model pool, quality scores & ELO rankings' },
  routing: { title: 'Routing Strategy', subtitle: 'Custom preference chains & ladder configuration' },
  ladders: { title: 'Model Ladders', subtitle: 'Drag-and-drop custom fallback chains per virtual model' },
  rl: { title: 'Adaptive RL', subtitle: 'LinUCB contextual bandit telemetry & feature weights' },
}

export default function App() {
  const {
    ready, authed, showAuth, session, applySession, logout, isAdmin, status, email,
  } = useAuth()
  const [page, setPage] = useState('dashboard')
  const [toast, setToast] = useState<{ msg: string; type: 'ok' | 'err' } | null>(null)
  const [refreshing, setRefreshing] = useState(false)
  const sse = useSSE()

  function showToast(msg: string, type: 'ok' | 'err' = 'ok') {
    setToast({ msg, type })
  }

  async function handleRefreshAll() {
    if (!isAdmin) return
    setRefreshing(true)
    showToast('Refreshing catalog & rankings...')
    const r = await ap('/admin/catalog/refresh', {})
    setRefreshing(false)
    if (r && (r as Record<string, unknown>).ok !== false) {
      showToast('Catalog & model ladder updated successfully')
    } else {
      showToast('Catalog refresh finished with warnings', 'err')
    }
  }

  const handlePageChange = useCallback((p: string) => setPage(p), [])

  const refreshSession = useCallback(async () => {
    const me = await api<AuthSession>('/auth/me')
    if (me?.authenticated) applySession(me)
  }, [applySession])

  const pending = status === 'pending_approval' || status === 'unverified'
  const pageMeta = PAGE_META[page] || { title: page, subtitle: 'System Module' }

  if (!ready) {
    return (
      <div className="h-screen w-screen flex flex-col items-center justify-center bg-zinc-950 text-zinc-400 gap-4">
        <div className="w-10 h-10 border-2 border-violet-500/30 border-t-violet-500 rounded-full animate-spin" />
        <span className="text-sm font-medium tracking-wide">Initializing Nimmakai Gateway…</span>
      </div>
    )
  }

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-zinc-950 text-zinc-100 font-sans antialiased">
      {/* Sidebar */}
      <Sidebar
        page={page}
        onNavigate={handlePageChange}
        isAdmin={isAdmin}
        email={email}
        onLogout={logout}
      />

      {/* Main Content Area */}
      <div className="flex-1 flex flex-col min-w-0 bg-zinc-950 relative overflow-hidden">
        {/* Top Navigation Header */}
        <header className="h-16 border-b border-white/[0.08] flex items-center justify-between px-8 bg-zinc-950/80 backdrop-blur-xl z-10 shrink-0">
          <div>
            <h2 className="text-base font-bold text-white tracking-tight">{pageMeta.title}</h2>
            <p className="text-[11px] text-zinc-400 font-medium">{pageMeta.subtitle}</p>
          </div>

          <div className="flex items-center gap-4">
            {/* Live Connection Status Badge */}
            <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-zinc-900 border border-white/[0.08] text-xs">
              <span className={`w-2 h-2 rounded-full ${sse ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.8)] animate-pulse' : 'bg-zinc-500'}`} />
              <span className="text-zinc-300 font-mono text-[11px]">
                {sse ? (
                  <span className="flex items-center gap-1">
                    <Radio className="w-3 h-3 text-emerald-400" />
                    <strong className="text-white">{sse.live_models}</strong> models · <strong className="text-white">{sse.active_providers}</strong> providers
                  </span>
                ) : authed ? 'Connecting SSE…' : 'Disconnected'}
              </span>
            </div>

            {/* Quick Admin Actions */}
            {isAdmin && (
              <Button
                variant="default"
                size="sm"
                onClick={handleRefreshAll}
                disabled={refreshing}
                title="Trigger catalog & ladder re-ranking"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin text-violet-400' : ''}`} />
                <span>Refresh Catalog</span>
              </Button>
            )}
          </div>
        </header>

        {/* Warning Banner for Unapproved / Unverified Accounts */}
        {pending && (
          <div className="mx-8 mt-4 rounded-2xl border border-amber-500/30 bg-amber-500/10 p-4 text-xs text-amber-200 flex items-center gap-3">
            <ShieldAlert className="w-5 h-5 text-amber-400 shrink-0" />
            <div>
              <strong className="font-semibold text-amber-100">Account Action Needed: </strong>
              {status === 'unverified'
                ? 'Please check your email to verify your address. Once verified, an administrator will approve your API access.'
                : 'Your account is currently pending administrator approval. API keys will be issued once approved.'}
            </div>
          </div>
        )}

        {/* Dynamic Page Container */}
        <main className="flex-1 overflow-y-auto p-8 custom-scrollbar">
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
          {page === 'ladders' && isAdmin && <ModelLaddersPage />}
          {page === 'rl' && isAdmin && <RLPage />}
          {page === 'playground' && <PlaygroundPage />}
        </main>
      </div>

      {/* Modals & Toasts */}
      {showAuth && !authed && (
        <AuthModal onSession={applySession} />
      )}

      {toast && (
        <Toast message={toast.msg} type={toast.type} onDismiss={() => setToast(null)} />
      )}
    </div>
  )
}
