import React, { useCallback, useEffect, useState } from 'react'
import { api, ap, errMsg, okBody } from '../lib/api'
import { Button, Card, CardBody, Badge, StatusDot, Select, CopyButton, Skeleton, ErrorState, EmptyState } from '../components/ui'
import {
  Users,
  CheckCircle2,
  XCircle,
  Slash,
  Key,
  ShieldCheck,
  UserCheck,
  UserX
} from 'lucide-react'

type UserRow = {
  id: string
  email: string
  role: string
  status: string
  created_at: number
  verified_at?: number | null
  approved_at?: number | null
}

export default function UsersPage() {
  const [users, setUsers] = useState<UserRow[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState('pending_approval')
  const [msg, setMsg] = useState('')
  const [issued, setIssued] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    const q = filter ? `?status=${encodeURIComponent(filter)}` : ''
    const r = await api<{ users: UserRow[] }>(`/admin/users${q}`)
    if (r?.users) setUsers(r.users)
    else {
      setUsers([])
      setError(errMsg(r) || 'Failed to load users')
    }
    setLoading(false)
  }, [filter])

  useEffect(() => { load() }, [load])

  async function approve(id: string) {
    setMsg('')
    setIssued(null)
    const r = await ap<{ api_key?: string; message?: string }>(`/admin/users/${id}/approve`, {})
    if (!okBody(r)) {
      setMsg(errMsg(r, 'Approve failed'))
      return
    }
    if (r?.api_key) setIssued(r.api_key)
    setMsg(r?.message || 'Account approved successfully')
    await load()
  }

  async function reject(id: string) {
    const r = await ap(`/admin/users/${id}/reject`, {})
    if (!okBody(r)) setMsg(errMsg(r, 'Reject failed'))
    else setMsg('Account registration rejected')
    await load()
  }

  async function suspend(id: string) {
    const r = await ap(`/admin/users/${id}/suspend`, {})
    if (!okBody(r)) setMsg(errMsg(r, 'Suspend failed'))
    else setMsg('User account suspended')
    await load()
  }

  return (
    <div className="space-y-6 animate-[fadeIn_0.25s_ease-out]">
      {/* Header controls */}
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <div className="flex items-center gap-2">
            <Users className="w-5 h-5 text-violet-400" />
            <h2 className="text-lg font-bold text-white tracking-tight">User Account Approvals</h2>
          </div>
          <p className="text-zinc-400 text-xs mt-1">Review user account signups and issue API credentials.</p>
        </div>
        <Select
          className="max-w-[200px]"
          value={filter}
          onChange={e => setFilter(e.target.value)}
        >
          <option value="pending_approval">Pending Approval</option>
          <option value="active">Active Users</option>
          <option value="unverified">Unverified Emails</option>
          <option value="rejected">Rejected</option>
          <option value="suspended">Suspended</option>
          <option value="">All Users</option>
        </Select>
      </div>

      {msg && (
        <div className="p-4 rounded-xl text-xs bg-zinc-900 border border-white/[0.08] text-zinc-200 flex items-center justify-between">
          <span>{msg}</span>
          <button className="text-zinc-500 hover:text-white" onClick={() => setMsg('')}>Dismiss</button>
        </div>
      )}

      {issued && (
        <div className="rounded-2xl border border-amber-500/30 bg-amber-500/10 p-5 text-xs text-amber-200 space-y-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 font-bold text-amber-100">
              <Key className="w-4 h-4 text-amber-400" />
              <span>Newly Issued API Key (Copy now — only shown once)</span>
            </div>
            <CopyButton text={issued} />
          </div>
          <code className="block bg-black/40 p-3 rounded-xl font-mono text-amber-100 break-all border border-amber-500/20">
            {issued}
          </code>
        </div>
      )}

      <Card>
        <CardBody className="p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs min-w-[560px]">
              <thead>
                <tr className="border-b border-white/[0.08] text-[10px] uppercase tracking-wider text-zinc-400 bg-white/[0.01]">
                  <th className="px-4 sm:px-6 py-3.5 font-semibold">User Email</th>
                  <th className="px-4 sm:px-6 py-3.5 font-semibold">Status</th>
                  <th className="px-4 sm:px-6 py-3.5 font-semibold">Role</th>
                  <th className="px-4 sm:px-6 py-3.5 font-semibold">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.06]">
                {loading && users.length === 0 ? (
                  Array.from({ length: 5 }).map((_, i) => (
                    <tr key={i}>
                      <td className="px-4 sm:px-6 py-4"><Skeleton lines={1} className="w-40" /></td>
                      <td className="px-4 sm:px-6 py-4"><Skeleton lines={1} className="w-20" /></td>
                      <td className="px-4 sm:px-6 py-4"><Skeleton lines={1} className="w-16" /></td>
                      <td className="px-4 sm:px-6 py-4"><Skeleton lines={1} className="w-24" /></td>
                    </tr>
                  ))
                ) : users.length === 0 ? (
                  <tr>
                    <td colSpan={4}>
                      <EmptyState title="No user accounts found" icon={Users}>
                        No accounts match the selected status filter.
                      </EmptyState>
                    </td>
                  </tr>
                ) : (
                  users.map(u => (
                    <tr key={u.id} className="hover:bg-white/[0.02] transition-colors">
                      <td className="px-4 sm:px-6 py-4 font-semibold text-white">{u.email}</td>
                      <td className="px-4 sm:px-6 py-4">
                        <Badge variant={u.status === 'active' ? 'ok' : u.status === 'pending_approval' ? 'warn' : 'err'}>
                          <StatusDot ok={u.status === 'active'} />
                          {u.status}
                        </Badge>
                      </td>
                      <td className="px-4 sm:px-6 py-4">
                        <Badge variant={u.role === 'admin' ? 'purple' : 'default'}>{u.role}</Badge>
                      </td>
                      <td className="px-4 sm:px-6 py-4">
                        <div className="flex gap-2 flex-wrap">
                          {u.status === 'pending_approval' && (
                            <>
                              <Button size="xs" variant="primary" onClick={() => approve(u.id)}>
                                <UserCheck className="w-3 h-3" />
                                <span>Approve</span>
                              </Button>
                              <Button size="xs" variant="danger" onClick={() => reject(u.id)}>
                                <UserX className="w-3 h-3" />
                                <span>Reject</span>
                              </Button>
                            </>
                          )}
                          {u.status === 'active' && (
                            <Button size="xs" variant="danger" onClick={() => suspend(u.id)}>
                              <Slash className="w-3 h-3" />
                              <span>Suspend</span>
                            </Button>
                          )}
                          {(u.status === 'rejected' || u.status === 'suspended') && (
                            <Button size="xs" variant="primary" onClick={() => approve(u.id)}>
                              <UserCheck className="w-3 h-3" />
                              <span>Re-Approve</span>
                            </Button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </CardBody>
      </Card>

      {error && !users.length && (
        <ErrorState title="Could not load users" message={error} onRetry={load} />
      )}
    </div>
  )
}
