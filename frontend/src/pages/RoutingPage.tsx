import { Card, CardHeader, CardBody, Badge, Button, Spinner } from '../components/ui'
import { useRankings, usePreferences } from '../hooks/useApi'
import { ap, errMsg } from '../lib/api'
import { useState } from 'react'

const INTENTS: Record<string, { label: string; desc: string }> = {
  coding_agentic: { label: 'Coding / Agentic', desc: 'Tools, multi-file, agents' },
  chat_fast: { label: 'Chat / Q&A', desc: 'Conversation, summaries' },
  reasoning: { label: 'Reasoning', desc: 'Math, logic, deep steps' },
  long_horizon: { label: 'Long Context', desc: 'Large context, planning' },
  vision: { label: 'Vision', desc: 'Image + text' },
  embeddings: { label: 'Embeddings', desc: 'Embedding models only' },
}

export default function RoutingPage() {
  const { data: rankings, reload: reloadRankings } = useRankings()
  const { prefs, reload: reloadPrefs } = usePreferences()
  const [msg, setMsg] = useState<string | null>(null)

  async function handleRefreshRankings() {
    const r = await ap('/admin/rankings/refresh', {})
    if (r && (r as Record<string, unknown>).ok) {
      setMsg('Rankings recomputed')
      reloadRankings()
    }
  }

  async function handleClearPref(intent: string) {
    await ap(`/preferences/${intent}`, undefined)
    reloadPrefs()
  }

  if (!rankings) return <Spinner />

  const ladders = rankings.ladders || {}
  const ladderKeys = Object.keys(ladders).filter(k => !k.includes('::'))

  return (
    <div className="animate-[fadeIn_0.3s_ease]">
      {msg && (
        <div className="mb-4 p-3 rounded-lg text-[13px] bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
          {msg}
          <button className="ml-3 text-xs opacity-60" onClick={() => setMsg(null)}>dismiss</button>
        </div>
      )}

      <div className="mb-8">
        <div className="flex items-center gap-3 mb-2">
          <h2 className="text-xl font-semibold">Intent Routing</h2>
          <Button size="sm" onClick={handleRefreshRankings}>Recompute Rankings</Button>
        </div>
        <p className="text-zinc-400 text-[13px]">Override automatic routing by pinning models to intents.</p>

        <div className="mt-6 space-y-3">
          {Object.entries(INTENTS).map(([key, meta]) => {
            const pref = prefs.find(p => p.intent === key)
            const ladder = ladders[key]
            const head = ladder?.ladder_head || []
            const scores = ladder?.scores_head || {}

            return (
              <Card key={key}>
                <CardBody className="flex items-center gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <strong className="text-sm">{meta.label}</strong>
                      <Badge variant="accent">{ladder?.ladder_len ?? 0} models</Badge>
                      {pref && <Badge variant="ok">Custom pinned</Badge>}
                    </div>
                    <p className="text-xs text-zinc-400 mb-2">{meta.desc}</p>
                    <div className="flex flex-wrap gap-1.5">
                      {head.slice(0, 5).map((m, i) => (
                        <span key={m} className={`inline-flex items-center px-2 py-1 rounded-full text-[11px] font-medium gap-1 ${i === 0 ? 'bg-violet-500/10 text-violet-300 border border-violet-500/20' : 'bg-white/[0.03] text-zinc-300 border border-white/[0.08]'}`}>
                          {i === 0 && '★ '}{m.split('/').pop()}
                          {scores[m] != null && <span className="text-zinc-500 ml-0.5">{Number(scores[m]).toFixed(1)}</span>}
                        </span>
                      ))}
                      {(ladder?.ladder_len ?? 0) > 5 && (
                        <span className="text-[11px] text-zinc-500 self-center">+{(ladder?.ladder_len ?? 0) - 5} more</span>
                      )}
                    </div>
                  </div>
                  <div className="flex gap-2">
                    {pref && (
                      <Button size="sm" variant="danger" onClick={() => handleClearPref(key)}>Clear Override</Button>
                    )}
                  </div>
                </CardBody>
              </Card>
            )
          })}
        </div>
      </div>

      {rankings.score_breakdown && rankings.score_breakdown.length > 0 && (
        <Card>
          <CardHeader>
            <h3 className="text-sm font-semibold">Coding Chain Score Breakdown</h3>
          </CardHeader>
          <CardBody className="p-0">
            <table className="w-full">
              <thead>
                <tr className="text-left border-b border-white/[0.08]">
                  <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px]">Model</th>
                  <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px]">Score</th>
                  <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px]">Intelligence</th>
                  <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px]">Speed</th>
                  <th className="px-6 py-3 text-[11px] font-semibold text-zinc-400 uppercase tracking-[1px]">Health</th>
                </tr>
              </thead>
              <tbody>
                {rankings.score_breakdown.map(s => (
                  <tr key={s.model} className="border-b border-white/[0.08] last:border-0 hover:bg-white/[0.01]">
                    <td className="px-6 py-3 text-[13px]">{s.model.split('/').pop()}</td>
                    <td className="px-6 py-3 text-[13px] font-semibold">{s.score.toFixed(4)}</td>
                    <td className="px-6 py-3 text-[13px] text-zinc-400">{s.intelligence.toFixed(3)}</td>
                    <td className="px-6 py-3 text-[13px] text-zinc-400">{s.speed.toFixed(3)}</td>
                    <td className="px-6 py-3">
                      {s.unhealthy ? <Badge variant="err">Unhealthy</Badge> : <span className="text-[13px] text-zinc-400">{s.health.toFixed(3)}</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardBody>
        </Card>
      )}
    </div>
  )
}
