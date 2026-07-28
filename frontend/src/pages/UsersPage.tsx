import React, { useCallback, useEffect, useState } from 'react'
import { api, ap, errMsg, okBody } from '../lib/api'
import {
  Button,
  Card,
  CardBody,
  Badge,
  StatusDot,
  Select,
  CopyButton,
  Skeleton,
  ErrorState,
  EmptyState,
  Modal
} from '../components/ui'
import {
  Users,
  CheckCircle2,
  XCircle,
  Slash,
  Key,
  ShieldCheck,
  UserCheck,
  UserX,
  RotateCw,
  Eye,
  ShieldAlert,
  UserCog,
  Trash2
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

type UserKeyRow = {
  id: string
  key_prefix: string
  name: string
  created_at: number
  revoked_at?: number | null
  last_used_at?: number | null
}

export default function UsersPage() {
  const [users, setUsers] = useState<UserRow[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState('')
  const [msg, setMsg] = useState('')
  const [issued, setIssued] = useState<{ email: string; key: string } | null>(null)

  // User detail modal & key management
  const [selectedUser, setSelectedUser] = useState<UserRow | null>(null)
  const [userKeys, setUserKeys] = useState<UserKeyRow[]>([])
  const [loadingKeys, setLoadingKeys] = useState(false)
  const [actionLoading, setActionLoading] = useState<string | null>(null)

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

  useEffect(() => {
    load()
  }, [load])

  async function approve(u: UserRow) {
    setMsg('')
    setIssued(null)
    setActionLoading(u.id)
    const r = await ap<{ api_key?: string; message?: string }>(`/admin/users/${u.id}/approve`, {})
    setActionLoading(null)
    if (!okBody(r)) {
      setMsg(errMsg(r, 'Approve failed'))
      return
    }
    if (r?.api_key) setIssued({ email: u.email, key: r.api_key })
    setMsg(r?.message || `Approved account for ${u.email}`)
    await load()
  }

  async function reject(u: UserRow) {
    setMsg('')
    setActionLoading(u.id)
    const r = await ap(`/admin/users/${u.id}/reject`, {})
    setActionLoading(null)
    if (!okBody(r)) setMsg(errMsg(r, 'Reject failed'))
    else setMsg(`Rejected registration for ${u.email}`)
    await load()
  }

  async function suspend(u: UserRow) {
    setMsg('')
    setActionLoading(u.id)
    const r = await ap(`/admin/users/${u.id}/suspend`, {})
    setActionLoading(null)
    if (!okBody(r)) setMsg(errMsg(r, 'Suspend failed'))
    else setMsg(`Suspended account for ${u.email}`)
    await load()
  }

  async function rotateKey(u: UserRow) {
    setMsg('')
    setIssued(null)
    setActionLoading(u.id)
    const r = await ap<{ api_key?: string; message?: string }>(`/admin/users/${u.id}/rotate-key`, {})
    setActionLoading(null)
    if (!okBody(r)) {
      setMsg(errMsg(r, 'Key rotation failed'))
      return
    }
    if (r?.api_key) setIssued({ email: u.email, key: r.api_key })
    setMsg(`Rotated API key for ${u.email}`)
    await load()
    if (selectedUser?.id === u.id) fetchUserKeys(u.id)
  }

  async function toggleRole(u: UserRow) {
    const newRole = u.role === 'admin' ? 'user' : 'admin'
    setMsg('')
    setActionLoading(u.id)
    const r = await ap(`/admin/users/${u.id}/role`, { role: newRole })
    setActionLoading(null)
    if (!okBody(r)) {
      setMsg(errMsg(r, 'Role update failed'))
      return
    }
    setMsg(`Updated ${u.email} role to ${newRole}`)
    await load()
  }

  async function fetchUserKeys(userId: string) {
    setLoadingKeys(true)
    const r = await api<{ keys: UserKeyRow[] }>(`/admin/users/${userId}/keys`)
    if (r?.keys) setUserKeys(r.keys)
    else setUserKeys([])
    setLoadingKeys(false)
  }

  async function revokeUserKey(userId: string, keyId: string) {
    const r = await ap(`/admin/users/${userId}/keys/${keyId}/revoke`, {})
    if (!okBody(r)) {
      setMsg(errMsg(r, 'Revoke key failed'))
      return
    }
    setMsg('API Key revoked successfully')
    fetchUserKeys(userId)
  }

  function openUserModal(u: UserRow) {
    setSelectedUser(u)
    fetchUserKeys(u.id)
  }

  return (
    <div className="space-y-6 animate-[fadeIn_0.25s_ease-out]">
      {/* Header controls */}
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <div className="flex items-center gap-2">
            <Users className="w-5 h-5 text-violet-400" />
            <h2 className="text-lg font-bold text-white tracking-tight">User Account & Key Administration</h2>
          </div>
          <p className="text-zinc-400 text-xs mt-1">
            Approve signups, suspend accounts, rotate user keys, and assign admin privileges.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Select
            className="max-w-[200px]"
            value={filter}
            onChange={e => setFilter(e.target.value)}
          >
            <option value="">All Account Statuses</option>
            <option value="pending_approval">Pending Approval</option>
            <option value="active">Active Users</option>
            <option value="unverified">Unverified Email</option>
            <option value="suspended">Suspended / Blocked</option>
            <option value="rejected">Rejected</option>
          </Select>
          <Button size="xs" variant="secondary" onClick={load}>
            <RotateCw className="w-3.5 h-3.5" />
            <span>Refresh</span>
          </Button>
        </div>
      </div>

      {msg && (
        <div className="p-4 rounded-xl text-xs bg-zinc-900 border border-white/[0.08] text-zinc-200 flex items-center justify-between">
          <span>{msg}</span>
          <button className="text-zinc-500 hover:text-white" onClick={() => setMsg('')}>
            Dismiss
          </button>
        </div>
      )}

      {issued && (
        <div className="rounded-2xl border border-emerald-500/30 bg-emerald-500/10 p-5 text-xs text-emerald-200 space-y-2">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 font-bold text-emerald-100">
              <Key className="w-4 h-4 text-emerald-400" />
              <span>Newly Issued API Key for {issued.email} (Copy now — only shown once)</span>
            </div>
            <CopyButton text={issued.key} />
          </div>
          <code className="block bg-black/40 p-3 rounded-xl font-mono text-emerald-100 break-all border border-emerald-500/20">
            {issued.key}
          </code>
        </div>
      )}

      <Card>
        <CardBody className="p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs min-w-[700px]">
              <thead>
                <tr className="border-b border-white/[0.08] text-[10px] uppercase tracking-wider text-zinc-400 bg-white/[0.01]">
                  <th className="px-4 sm:px-6 py-3.5 font-semibold">User Email</th>
                  <th className="px-4 sm:px-6 py-3.5 font-semibold">Status</th>
                  <th className="px-4 sm:px-6 py-3.5 font-semibold">Role</th>
                  <th className="px-4 sm:px-6 py-3.5 font-semibold">Registered</th>
                  <th className="px-4 sm:px-6 py-3.5 font-semibold text-right">Actions & Controls</th>
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
                      <td className="px-4 sm:px-6 py-4"><Skeleton lines={1} className="w-32 ml-auto" /></td>
                    </tr>
                  ))
                ) : users.length === 0 ? (
                  <tr>
                    <td colSpan={5}>
                      <EmptyState title="No user accounts found" icon={Users}>
                        No accounts match the selected status filter.
                      </EmptyState>
                    </td>
                  </tr>
                ) : (
                  users.map(u => (
                    <tr key={u.id} className="hover:bg-white/[0.02] transition-colors">
                      <td className="px-4 sm:px-6 py-4 font-semibold text-white">
                        <div className="flex items-center gap-2">
                          <span>{u.email}</span>
                        </div>
                      </td>
                      <td className="px-4 sm:px-6 py-4">
                        <Badge
                          variant={
                            u.status === 'active'
                              ? 'ok'
                              : u.status === 'pending_approval'
                              ? 'warn'
                              : 'err'
                          }
                        >
                          <StatusDot ok={u.status === 'active'} />
                          {u.status}
                        </Badge>
                      </td>
                      <td className="px-4 sm:px-6 py-4">
                        <button
                          onClick={() => toggleRole(u)}
                          className="group focus:outline-none"
                          title="Click to toggle Admin / User role"
                        >
                          <Badge variant={u.role === 'admin' ? 'purple' : 'default'} className="group-hover:opacity-80 transition-opacity">
                            {u.role === 'admin' ? <ShieldCheck className="w-3 h-3 text-purple-300" /> : <UserCog className="w-3 h-3 text-zinc-400" />}
                            <span>{u.role}</span>
                          </Badge>
                        </button>
                      </td>
                      <td className="px-4 sm:px-6 py-4 text-zinc-400 font-mono text-[11px]">
                        {new Date(u.created_at * 1000).toLocaleDateString()}
                      </td>
                      <td className="px-4 sm:px-6 py-4 text-right">
                        <div className="flex gap-2 justify-end flex-wrap">
                          <Button size="xs" variant="secondary" onClick={() => openUserModal(u)}>
                            <Eye className="w-3 h-3" />
                            <span>Keys & Info</span>
                          </Button>

                          {u.status === 'pending_approval' && (
                            <>
                              <Button
                                size="xs"
                                variant="primary"
                                disabled={actionLoading === u.id}
                                onClick={() => approve(u)}
                              >
                                <UserCheck className="w-3 h-3" />
                                <span>Approve</span>
                              </Button>
                              <Button
                                size="xs"
                                variant="danger"
                                disabled={actionLoading === u.id}
                                onClick={() => reject(u)}
                              >
                                <UserX className="w-3 h-3" />
                                <span>Reject</span>
                              </Button>
                            </>
                          )}

                          {u.status === 'active' && (
                            <>
                              <Button
                                size="xs"
                                variant="secondary"
                                disabled={actionLoading === u.id}
                                onClick={() => rotateKey(u)}
                                title="Rotate API Key for user"
                              >
                                <RotateCw className="w-3 h-3 text-emerald-400" />
                                <span>Rotate Key</span>
                              </Button>
                              <Button
                                size="xs"
                                variant="danger"
                                disabled={actionLoading === u.id}
                                onClick={() => suspend(u)}
                                title="Suspend user account & invalidate active sessions"
                              >
                                <Slash className="w-3 h-3" />
                                <span>Suspend</span>
                              </Button>
                            </>
                          )}

                          {(u.status === 'rejected' || u.status === 'suspended') && (
                            <Button
                              size="xs"
                              variant="primary"
                              disabled={actionLoading === u.id}
                              onClick={() => approve(u)}
                            >
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

      {/* User Keys & Details Modal */}
      {selectedUser && (
        <Modal
          isOpen={true}
          onClose={() => setSelectedUser(null)}
          title={`User Controls: ${selectedUser.email}`}
        >
          <div className="space-y-5 text-xs">
            <div className="grid grid-cols-2 gap-3 p-3.5 rounded-xl bg-white/[0.02] border border-white/[0.06]">
              <div>
                <span className="text-zinc-400 text-[10px] uppercase font-bold block">User ID</span>
                <span className="font-mono text-zinc-200">{selectedUser.id}</span>
              </div>
              <div>
                <span className="text-zinc-400 text-[10px] uppercase font-bold block">Account Status</span>
                <Badge variant={selectedUser.status === 'active' ? 'ok' : 'warn'}>
                  {selectedUser.status}
                </Badge>
              </div>
              <div>
                <span className="text-zinc-400 text-[10px] uppercase font-bold block">Role</span>
                <span className="font-semibold text-zinc-200 capitalize">{selectedUser.role}</span>
              </div>
              <div>
                <span className="text-zinc-400 text-[10px] uppercase font-bold block">Created Date</span>
                <span className="font-mono text-zinc-300">{new Date(selectedUser.created_at * 1000).toLocaleString()}</span>
              </div>
            </div>

            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <h4 className="font-bold text-white text-xs flex items-center gap-1.5">
                  <Key className="w-4 h-4 text-emerald-400" />
                  <span>API Keys ({userKeys.length})</span>
                </h4>
                <Button size="xs" variant="primary" onClick={() => rotateKey(selectedUser)}>
                  <RotateCw className="w-3 h-3" />
                  <span>Rotate New Key</span>
                </Button>
              </div>

              {loadingKeys ? (
                <Skeleton lines={3} />
              ) : userKeys.length === 0 ? (
                <p className="text-zinc-500 italic text-xs py-2">No API keys issued for this account yet.</p>
              ) : (
                <div className="space-y-2">
                  {userKeys.map(k => (
                    <div
                      key={k.id}
                      className="p-3 rounded-xl bg-black/40 border border-white/[0.06] flex items-center justify-between gap-3"
                    >
                      <div className="space-y-0.5">
                        <div className="flex items-center gap-2">
                          <code className="font-mono text-emerald-400 font-bold text-xs">{k.key_prefix}...</code>
                          {k.revoked_at ? (
                            <Badge variant="err">Revoked</Badge>
                          ) : (
                            <Badge variant="ok">Active</Badge>
                          )}
                        </div>
                        <p className="text-[10px] text-zinc-500">
                          Created {new Date(k.created_at * 1000).toLocaleDateString()}
                          {k.last_used_at ? ` · Last used ${new Date(k.last_used_at * 1000).toLocaleDateString()}` : ''}
                        </p>
                      </div>

                      {!k.revoked_at && (
                        <Button
                          size="xs"
                          variant="danger"
                          onClick={() => revokeUserKey(selectedUser.id, k.id)}
                          title="Revoke this API Key"
                        >
                          <Trash2 className="w-3 h-3" />
                          <span>Revoke</span>
                        </Button>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>

            <div className="pt-2 border-t border-white/[0.08] flex justify-between gap-2">
              <Button
                size="xs"
                variant={selectedUser.role === 'admin' ? 'secondary' : 'primary'}
                onClick={() => toggleRole(selectedUser)}
              >
                <ShieldCheck className="w-3.5 h-3.5" />
                <span>{selectedUser.role === 'admin' ? 'Demote to User' : 'Promote to Admin'}</span>
              </Button>
              <Button size="xs" variant="secondary" onClick={() => setSelectedUser(null)}>
                Close
              </Button>
            </div>
          </div>
        </Modal>
      )}

      {error && !users.length && (
        <ErrorState title="Could not load users" message={error} onRetry={load} />
      )}
    </div>
  )
}
