import React, { useState } from 'react'
import { Card, CardHeader, CardBody, Badge, Button, ErrorState, Skeleton } from '../components/ui'
import { useRankings, usePreferences } from '../hooks/useApi'
import { ap, ad, errMsg } from '../lib/api'
import {
  GitFork,
  RefreshCw,
  Star,
  Trash2,
  Layers,
  Cpu,
  Zap,
  CheckCircle2,
  Activity
} from 'lucide-react'

const INTENTS: Record<string, { label: string; desc: string }> = {
  coding_agentic: { label: 'Coding & Agentic Operations', desc: 'Code syntax, multi-file agents, tool usage' },
  chat_fast: { label: 'Interactive Chat & Q&A', desc: 'Conversational turns, summaries, general queries' },
  reasoning: { label: 'Deep Reasoning & Math', desc: 'Complex logic proofs, multi-step chain-of-thought' },
  long_horizon: { label: 'Long Context Processing', desc: 'Large document synthesis, project planning' },
  vision: { label: 'Multimodal Vision', desc: 'Image comprehension, OCR, multi-modal reasoning' },
  embeddings: { label: 'Vector Embeddings', desc: 'High-dimensional semantic embeddings' },
}

export default function RoutingPage() {
  const { data: rankings, reload: reloadRankings, loading: rankingsLoading, error: rankingsError } = useRankings()
  const { data: prefsData, reload: reloadPrefs, loading: prefsLoading, error: prefsError } = usePreferences()
  const prefs = prefsData?.preferences || []
  const [msg, setMsg] = useState<string | null>(null)

  async function handleRefreshRankings() {
    const r = await ap('/admin/rankings/refresh', {})
    if (r && (r as Record<string, unknown>).ok) {
      setMsg('Routing engine recomputed model ladder rankings')
      reloadRankings()
    }
  }

  async function handleClearPref(intent: string) {
    await ad(`/preferences/${intent}`)
    reloadPrefs()
  }

  if (rankingsLoading || prefsLoading) return (
    <div className="space-y-6 animate-[fadeIn_0.25s_ease-out]">
      <div className="flex items-center gap-2">
        <GitFork className="w-5 h-5 text-violet-400" />
        <h2 className="text-lg font-bold text-white tracking-tight">Intent-Based Dynamic Routing Engine</h2>
      </div>
      <Skeleton lines={2} className="max-w-2xl" />
      <Skeleton cards={3} />
    </div>
  )
  if (rankingsError) return <ErrorState title="Routing data failed" message={rankingsError} onRetry={reloadRankings} />
  if (!rankings) return <ErrorState title="No routing data" message="The rankings endpoint returned an empty response." />

  const ladders = rankings.ladders || {}

  return (
    <div className="space-y-6 animate-[fadeIn_0.25s_ease-out]">
      {/* Header controls */}
      <div className="flex justify-between items-start gap-4 flex-wrap">
        <div>
          <div className="flex items-center gap-2">
            <GitFork className="w-5 h-5 text-violet-400" />
            <h2 className="text-lg font-bold text-white tracking-tight">Intent-Based Dynamic Routing Engine</h2>
          </div>
          <p className="text-zinc-400 text-xs mt-1 max-w-[620px]">
            Potato dynamically ranks available models for each task intent based on benchmark capability, measured tokens/sec, and real-time provider health.
          </p>
        </div>
        <Button variant="secondary" onClick={handleRefreshRankings}>
          <RefreshCw className="w-3.5 h-3.5 text-violet-400" />
          <span>Recompute Rankings</span>
        </Button>
      </div>

      {msg && (
        <div className="p-4 rounded-xl text-xs bg-emerald-500/10 text-emerald-300 border border-emerald-500/20 flex justify-between items-center font-medium">
          <span>{msg}</span>
          <button className="text-zinc-400 hover:text-white" onClick={() => setMsg(null)}>Dismiss</button>
        </div>
      )}

      {/* Intent Cards */}
      <div className="space-y-4">
        {Object.entries(INTENTS).map(([key, meta]) => {
          const pref = prefs.find(p => p.intent === key)
          const ladder = ladders[key]
          const head = ladder?.ladder_head || []
          const scores = ladder?.scores_head || {}

          return (
            <Card key={key}>
              <CardBody className="flex items-center justify-between gap-6 flex-wrap">
                <div className="flex-1 min-w-[280px]">
                  <div className="flex items-center gap-2.5 mb-1">
                    <strong className="text-sm font-bold text-white">{meta.label}</strong>
                    <Badge variant="accent" className="font-mono">{ladder?.ladder_len ?? 0} models ranked</Badge>
                    {pref && <Badge variant="ok">Custom Pinned</Badge>}
                  </div>
                  <p className="text-xs text-zinc-400 mb-3">{meta.desc}</p>
                  
                  {/* Model Ladder Chips */}
                  <div className="flex flex-wrap gap-2">
                    {head.slice(0, 5).map((m, i) => (
                      <span
                        key={m}
                        className={`inline-flex items-center px-3 py-1 rounded-full text-xs font-mono font-medium gap-1.5 transition-all ${
                          i === 0
                            ? 'bg-violet-500/20 text-violet-200 border border-violet-500/30 shadow-[0_0_12px_rgba(139,92,246,0.2)]'
                            : 'bg-zinc-950 text-zinc-300 border border-white/[0.08]'
                        }`}
                      >
                        {i === 0 && <Star className="w-3 h-3 text-amber-400 fill-amber-400" />}
                        <span>{m.split('/').pop()}</span>
                        {scores[m] != null && <span className="text-zinc-500 font-normal">({Number(scores[m]).toFixed(1)})</span>}
                      </span>
                    ))}
                    {(ladder?.ladder_len ?? 0) > 5 && (
                      <span className="text-xs text-zinc-500 font-mono self-center">
                        +{(ladder?.ladder_len ?? 0) - 5} remaining
                      </span>
                    )}
                  </div>
                </div>

                <div className="flex items-center gap-2">
                  {pref && (
                    <Button size="sm" variant="danger" onClick={() => handleClearPref(key)}>
                      <Trash2 className="w-3.5 h-3.5" />
                      <span>Clear Custom Override</span>
                    </Button>
                  )}
                </div>
              </CardBody>
            </Card>
          )
        })}
      </div>

      {/* Coding Score Breakdown Table */}
      {rankings.score_breakdown && rankings.score_breakdown.length > 0 && (
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Zap className="w-4 h-4 text-violet-400" />
              <h3 className="text-sm font-semibold text-white">Coding Agentic Score Matrix Breakdown</h3>
            </div>
          </CardHeader>
          <CardBody className="p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead>
                  <tr className="border-b border-white/[0.08] text-[10px] uppercase tracking-wider text-zinc-400 bg-white/[0.01]">
                    <th className="px-6 py-3.5 font-semibold">Model Name</th>
                    <th className="px-6 py-3.5 font-semibold">Composite Score</th>
                    <th className="px-6 py-3.5 font-semibold">Intelligence Signal</th>
                    <th className="px-6 py-3.5 font-semibold">Speed Signal</th>
                    <th className="px-6 py-3.5 font-semibold">Health Score</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/[0.06]">
                  {rankings.score_breakdown.map(s => (
                    <tr key={s.model} className="hover:bg-white/[0.02] transition-colors">
                      <td className="px-6 py-4 font-mono font-semibold text-white">{s.model.split('/').pop()}</td>
                      <td className="px-6 py-4 font-mono text-violet-300 font-bold">{s.score.toFixed(4)}</td>
                      <td className="px-6 py-4 font-mono text-zinc-300">{s.intelligence.toFixed(3)}</td>
                      <td className="px-6 py-4 font-mono text-zinc-300">{s.speed.toFixed(3)}</td>
                      <td className="px-6 py-4">
                        {s.unhealthy ? (
                          <Badge variant="err">Unhealthy</Badge>
                        ) : (
                          <span className="font-mono text-emerald-400 font-semibold">{s.health.toFixed(3)}</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardBody>
        </Card>
      )}
    </div>
  )
}
