import { useCallback, useEffect, useState } from 'react'
import { api, ap, errMsg, okBody } from '../lib/api'
import { Button } from '../components/ui'

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
  const [filter, setFilter] = useState('pending_approval')
  const [msg, setMsg] = useState('')
  const [issued, setIssued] = useState<string | null>(null)

  const load = useCallback(async () => {
    const q = filter ? `?status=${encodeURIComponent(filter)}` : ''
    const r = await api<{ users: UserRow[] }>(`/admin/users${q}`)
    if (r?.users) setUsers(r.users)
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
    setMsg(r?.message || 'Approved')
    await load()
  }

  async function reject(id: string) {
    const r = await ap(`/admin/users/${id}/reject`, {})
    if (!okBody(r)) setMsg(errMsg(r, 'Reject failed'))
    else setMsg('Rejected')
    await load()
  }

  async function suspend(id: string) {
    const r = await ap(`/admin/users/${id}/suspend`, {})
    if (!okBody(r)) setMsg(errMsg(r, 'Suspend failed'))
    else setMsg('Suspended')
    await load()
  }

  return (
    <div className="space-y-6 max-w-4xl">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h3 className="text-lg font-semibold">Users</h3>
          <p className="text-sm text-zinc-500 mt-1">Approve accounts before API keys are issued.</p>
        </div>
        <select
          className="bg-zinc-900 border border-white/[0.08] rounded-lg px-3 py-2 text-sm"
          value={filter}
          onChange={e => setFilter(e.target.value)}
        >
          <option value="pending_approval">Pending approval</option>
          <option value="active">Active</option>
          <option value="unverified">Unverified</option>
          <option value="rejected">Rejected</option>
          <option value="suspended">Suspended</option>
          <option value="">All</option>
        </select>
      </div>

      {msg && <p className="text-sm text-zinc-300">{msg}</p>}
      {issued && (
        <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-4 text-sm break-all">
          <p className="text-amber-200 font-medium mb-1">API key (copy now — shown once)</p>
          <code className="text-amber-100">{issued}</code>
        </div>
      )}

      <div className="rounded-xl border border-white/[0.08] overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-white/[0.03] text-zinc-400 text-left">
            <tr>
              <th className="px-4 py-3 font-medium">Email</th>
              <th className="px-4 py-3 font-medium">Status</th>
              <th className="px-4 py-3 font-medium">Role</th>
              <th className="px-4 py-3 font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            {users.length === 0 && (
              <tr>
                <td colSpan={4} className="px-4 py-8 text-center text-zinc-500">No users</td>
              </tr>
            )}
            {users.map(u => (
              <tr key={u.id} className="border-t border-white/[0.06]">
                <td className="px-4 py-3">{u.email}</td>
                <td className="px-4 py-3 text-zinc-400">{u.status}</td>
                <td className="px-4 py-3 text-zinc-400">{u.role}</td>
                <td className="px-4 py-3">
                  <div className="flex gap-2 flex-wrap">
                    {u.status === 'pending_approval' && (
                      <>
                        <Button variant="primary" onClick={() => approve(u.id)}>Approve</Button>
                        <Button onClick={() => reject(u.id)}>Reject</Button>
                      </>
                    )}
                    {u.status === 'active' && (
                      <Button onClick={() => suspend(u.id)}>Suspend</Button>
                    )}
                    {(u.status === 'rejected' || u.status === 'suspended') && (
                      <Button variant="primary" onClick={() => approve(u.id)}>Re-approve</Button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
