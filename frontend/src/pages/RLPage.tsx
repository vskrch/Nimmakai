import React, { useState, useEffect, useCallback, useMemo } from 'react'
import { Card, CardHeader, CardBody, Badge, Button, Spinner } from '../components/ui'
import { api } from '../lib/api'
import { Cpu, RefreshCw, RotateCcw, Zap, BarChart2, ShieldCheck } from 'lucide-react'

interface ModelRLStats {
  model_id: string
  request_count: number
  avg_reward: number
  total_reward: number
  theta: number[]
  last_updated: number
}

interface RLStatsResponse {
  models: ModelRLStats[]
  feature_names: string[]
  feature_dim: number
  enabled: boolean
}

export default function RLPage() {
  const [data, setData] = useState<RLStatsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [resetting, setResetting] = useState<string | null>(null)
  const [msg, setMsg] = useState<{ text: string; type: 'ok' | 'err' } | null>(null)

  const loadStats = useCallback(async () => {
    setLoading(true)
    try {
      const res = await api<RLStatsResponse>('/admin/rl/stats')
      if (res && Array.isArray(res.models)) {
        setData(res)
      } else {
        setData({ models: [], feature_names: [], feature_dim: 12, enabled: true })
      }
    } catch {
      setMsg({ text: 'Failed to fetch RL stats', type: 'err' })
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    loadStats()
  }, [loadStats])

  const handleReset = async (modelId?: string) => {
    setResetting(modelId || 'all')
    try {
      await api('/admin/rl/reset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_id: modelId }),
      })
      setMsg({
        text: modelId ? `Reset RL policy for ${modelId}` : 'Reset all RL model policies',
        type: 'ok',
      })
      await loadStats()
    } catch {
      setMsg({ text: 'Failed to reset RL policy', type: 'err' })
    } finally {
      setResetting(null)
    }
  }

  const models = data?.models || []
  const featureNames = data?.feature_names || [
    'Token Tier', 'Tool Density', 'Code Ratio', 'Python',
    'TypeScript', 'Go', 'Rust/C++', 'Agent Harness',
    'Multimodal', 'Reasoning', 'Turn Depth', 'Intent Prior'
  ]

  const totalDecisions = models.reduce((acc, m) => acc + (m.request_count || 0), 0)
  const globalAvgReward = models.length
    ? (models.reduce((acc, m) => acc + (m.avg_reward || 0), 0) / models.length).toFixed(2)
    : '0.00'

  const maxAbsTheta = useMemo(() => {
    let max = 0
    for (const m of models) {
      for (const w of m.theta) max = Math.max(max, Math.abs(w))
    }
    return Math.max(max, 0.1)
  }, [models])

  const heatGradient = (value: number) => {
    const ratio = value / maxAbsTheta // [-1 .. 1]
    if (ratio > 0) {
      const alpha = Math.min(1, Math.max(0.15, ratio))
      return `rgba(16, 185, 129, ${alpha})`
    }
    const alpha = Math.min(1, Math.max(0.15, Math.abs(ratio)))
    return `rgba(244, 63, 94, ${alpha})`
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold text-slate-100 flex items-center gap-2">
              <Cpu className="w-7 h-7 text-emerald-400" />
              Contextual Reinforcement Learning
            </h1>
            <Badge variant="ok" className="px-2.5 py-1 text-xs">
              LinUCB Bandit
            </Badge>
          </div>
          <p className="text-sm text-slate-400 mt-1">
            Real-time online feature weighting with Sherman-Morrison rank-1 updates for sub-microsecond routing optimization.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" onClick={loadStats} disabled={loading}>
            <RefreshCw className={`w-4 h-4 mr-1.5 ${loading ? 'animate-spin' : ''}`} />
            Refresh Telemetry
          </Button>
          <Button
            variant="danger"
            size="sm"
            onClick={() => handleReset()}
            disabled={resetting === 'all'}
          >
            <RotateCcw className="w-4 h-4 mr-1.5" />
            Reset All Policies
          </Button>
        </div>
      </div>

      {msg && (
        <div
          className={`p-3.5 rounded-lg text-sm flex items-center justify-between ${
            msg.type === 'ok'
              ? 'bg-emerald-950/40 text-emerald-300 border border-emerald-800/40'
              : 'bg-rose-950/40 text-rose-300 border border-rose-800/40'
          }`}
        >
          <span>{msg.text}</span>
          <button onClick={() => setMsg(null)} className="text-xs opacity-70 hover:opacity-100">
            Dismiss
          </button>
        </div>
      )}

      {/* Metric Cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card className="bg-slate-900/60 border-slate-800">
          <CardBody className="p-4 flex items-center justify-between">
            <div>
              <p className="text-xs font-medium text-slate-400">Active RL Models</p>
              <p className="text-2xl font-bold text-slate-100 mt-1">{models.length}</p>
            </div>
            <Zap className="w-8 h-8 text-amber-400/70" />
          </CardBody>
        </Card>

        <Card className="bg-slate-900/60 border-slate-800">
          <CardBody className="p-4 flex items-center justify-between">
            <div>
              <p className="text-xs font-medium text-slate-400">Total Adaptations</p>
              <p className="text-2xl font-bold text-slate-100 mt-1">{(totalDecisions ?? 0).toLocaleString()}</p>
            </div>
            <BarChart2 className="w-8 h-8 text-indigo-400/70" />
          </CardBody>
        </Card>

        <Card className="bg-slate-900/60 border-slate-800">
          <CardBody className="p-4 flex items-center justify-between">
            <div>
              <p className="text-xs font-medium text-slate-400">Average Policy Reward</p>
              <p className="text-2xl font-bold text-emerald-400 mt-1">
                {globalAvgReward} <span className="text-xs text-slate-400 font-normal">/ 1.00</span>
              </p>
            </div>
            <ShieldCheck className="w-8 h-8 text-emerald-400/70" />
          </CardBody>
        </Card>

        <Card className="bg-slate-900/60 border-slate-800">
          <CardBody className="p-4 flex items-center justify-between">
            <div>
              <p className="text-xs font-medium text-slate-400">Context Dimension</p>
              <p className="text-2xl font-bold text-slate-100 mt-1">12-D Dense</p>
            </div>
            <Cpu className="w-8 h-8 text-cyan-400/70" />
          </CardBody>
        </Card>
      </div>

      {/* Model Feature Vectors Table */}
      <Card className="bg-slate-900/60 border-slate-800">
        <CardHeader className="p-4 border-b border-slate-800 flex items-center justify-between">
          <h2 className="text-base font-semibold text-slate-100">Learned Feature Weights (θ)</h2>
          <span className="text-xs text-slate-400">Updated online after every execution</span>
        </CardHeader>
        <CardBody className="p-0 overflow-x-auto">
          {loading ? (
            <div className="p-12 text-center text-slate-400 flex flex-col items-center gap-2">
              <RefreshCw className="w-6 h-6 animate-spin text-emerald-400" />
              Loading LinUCB policy states...
            </div>
          ) : models.length === 0 ? (
            <div className="p-12 text-center text-slate-400">
              No RL policy data recorded yet. Send client requests through the gateway to initialize model feature weights.
            </div>
          ) : (
            <table className="w-full text-left text-xs min-w-[760px]">
              <thead className="bg-slate-950/60 text-slate-400 font-medium border-b border-slate-800">
                <tr>
                  <th className="p-3 sticky left-0 bg-slate-950/60 z-10">Model ID</th>
                  <th className="p-3 text-center">Requests</th>
                  <th className="p-3 text-center">Avg Reward</th>
                  {featureNames.map((name, idx) => (
                    <th key={idx} className="p-3 text-center font-mono text-[11px]" title={name}>
                      {name}
                    </th>
                  ))}
                  <th className="p-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60 text-slate-300">
                {models.map((m) => (
                  <tr key={m.model_id} className="hover:bg-slate-800/30 transition-colors">
                    <td className="p-3 font-mono font-medium text-slate-200 sticky left-0 bg-inherit z-10">{m.model_id}</td>
                    <td className="p-3 text-center font-mono">{m.request_count}</td>
                    <td className="p-3 text-center font-mono">
                      <span
                        className={`px-2 py-0.5 rounded text-xs ${
                          m.avg_reward >= 0.5
                            ? 'bg-emerald-950/60 text-emerald-300 border border-emerald-800/50'
                            : m.avg_reward >= 0.0
                            ? 'bg-amber-950/60 text-amber-300 border border-amber-800/50'
                            : 'bg-rose-950/60 text-rose-300 border border-rose-800/50'
                        }`}
                      >
                        {m.avg_reward.toFixed(2)}
                      </span>
                    </td>
                    {m.theta.map((weight, fIdx) => (
                      <td key={fIdx} className="p-3 text-center font-mono text-[11px]">
                        <span
                          className="inline-flex items-center justify-center w-12 h-6 rounded-md"
                          style={{ backgroundColor: heatGradient(weight) }}
                          title={`${featureNames[fIdx]}: ${weight.toFixed(3)}`}
                        >
                          <span className={`${Math.abs(weight) > maxAbsTheta * 0.4 ? 'text-white font-semibold' : 'text-slate-400'}`}>
                            {weight.toFixed(2)}
                          </span>
                        </span>
                      </td>
                    ))}
                    <td className="p-3 text-right">
                      <Button
                        variant="ghost"
                        size="xs"
                        onClick={() => handleReset(m.model_id)}
                        disabled={resetting === m.model_id}
                        className="text-slate-400 hover:text-rose-400"
                      >
                        <RotateCcw className="w-3.5 h-3.5" />
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </CardBody>
      </Card>
    </div>
  )
}
