import { useState, useRef, useEffect } from 'react'
import { Card, CardHeader, CardBody, Button, Input, Spinner } from '../components/ui'
import { useCatalog } from '../hooks/useApi'
import { getAuthKey } from '../lib/api'

interface Message {
  role: 'user' | 'assistant' | 'system'
  content: string
}

export default function PlaygroundPage() {
  const { data: catalog } = useCatalog()
  const [messages, setMessages] = useState<Message[]>([
    { role: 'assistant', content: "Hello! I'm ready. Type a message below to test the routing." }
  ])
  const [input, setInput] = useState('')
  const [model, setModel] = useState('auto')
  const [streaming, setStreaming] = useState(false)
  const [params, setParams] = useState({ temperature: 0.7, max_tokens: 1024 })
  const msgsRef = useRef<HTMLDivElement>(null)

  const allModels = catalog?.dynamic_chains?.coding_agentic || []

  useEffect(() => {
    if (msgsRef.current) {
      msgsRef.current.scrollTop = msgsRef.current.scrollHeight
    }
  }, [messages])

  async function sendMessage() {
    if (!input.trim() || streaming) return
    const userMsg: Message = { role: 'user', content: input.trim() }
    const newMsgs = [...messages, userMsg]
    setMessages(newMsgs)
    setInput('')
    setStreaming(true)

    try {
      const key = getAuthKey()
      const res = await fetch('/v1/chat/completions', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(key ? { Authorization: `Bearer ${key}` } : {}),
        },
        body: JSON.stringify({
          model: model === 'auto' ? undefined : model,
          messages: newMsgs.map(m => ({ role: m.role, content: m.content })),
          stream: true,
          temperature: params.temperature,
          max_tokens: params.max_tokens,
        }),
      })

      if (!res.ok) {
        const err = await res.text()
        setMessages([...newMsgs, { role: 'assistant', content: `Error ${res.status}: ${err}` }])
        setStreaming(false)
        return
      }

      const reader = res.body?.getReader()
      const decoder = new TextDecoder()
      let assistantContent = ''
      setMessages([...newMsgs, { role: 'assistant', content: '' }])

      if (reader) {
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          const chunk = decoder.decode(value, { stream: true })
          for (const line of chunk.split('\n')) {
            if (!line.startsWith('data: ')) continue
            const data = line.slice(6).trim()
            if (data === '[DONE]') continue
            try {
              const parsed = JSON.parse(data)
              const delta = parsed.choices?.[0]?.delta?.content
              if (delta) {
                assistantContent += delta
                setMessages([...newMsgs, { role: 'assistant', content: assistantContent }])
              }
            } catch { /* ignore malformed chunks */ }
          }
        }
      }
    } catch (e) {
      setMessages([...newMsgs, { role: 'assistant', content: `Network error: ${e}` }])
    }
    setStreaming(false)
  }

  return (
    <div className="animate-[fadeIn_0.3s_ease] flex gap-6 h-[calc(100vh-140px)]">
      <div className="flex-1 flex flex-col bg-white/[0.03] border border-white/[0.08] rounded-xl overflow-hidden">
        <div ref={msgsRef} className="flex-1 overflow-y-auto p-6 flex flex-col gap-4">
          {messages.map((msg, i) => (
            <div key={i} className={`flex gap-3 max-w-[85%] ${msg.role === 'user' ? 'self-end flex-row-reverse' : ''}`}>
              <div className={`w-8 h-8 rounded-lg shrink-0 flex items-center justify-center font-semibold text-xs ${msg.role === 'user' ? 'bg-gradient-to-br from-violet-500 to-fuchsia-500' : 'bg-white/10 border border-white/[0.08]'}`}>
                {msg.role === 'user' ? 'U' : 'N'}
              </div>
              <div className={`px-4 py-3 rounded-xl text-sm leading-relaxed whitespace-pre-wrap ${msg.role === 'user' ? 'bg-violet-500/10 border border-violet-500/20' : 'bg-white/[0.03] border border-white/[0.08]'}`}>
                {msg.content || (streaming && i === messages.length - 1 ? '...' : '')}
              </div>
            </div>
          ))}
        </div>
        <div className="p-4 border-t border-white/[0.08] bg-black/20 flex gap-3">
          <textarea
            className="flex-1 bg-transparent border border-white/[0.08] text-white px-3 py-3 rounded-lg text-sm resize-none outline-none focus:border-violet-500 font-[inherit]"
            rows={2}
            placeholder="Send a message... (Shift+Enter for newline)"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage() } }}
          />
          <Button variant="primary" onClick={sendMessage} disabled={streaming} className="self-end">
            {streaming ? '...' : 'Send'}
          </Button>
        </div>
      </div>

      <div className="w-[300px] flex flex-col gap-4 shrink-0 overflow-y-auto">
        <Card>
          <CardHeader><h3 className="text-sm font-semibold">Parameters</h3></CardHeader>
          <CardBody className="flex flex-col gap-4">
            <div>
              <label className="block text-xs text-zinc-400 mb-1.5">Model</label>
              <select
                className="w-full bg-black/20 border border-white/[0.08] text-white px-3 py-2 rounded-lg text-[13px] appearance-auto"
                value={model}
                onChange={e => setModel(e.target.value)}
              >
                <option value="auto">auto (Intelligent)</option>
                {allModels.slice(0, 20).map(m => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-zinc-400 mb-1.5">Temperature: {params.temperature}</label>
              <input
                type="range"
                min={0}
                max={2}
                step={0.1}
                value={params.temperature}
                onChange={e => setParams(p => ({ ...p, temperature: parseFloat(e.target.value) }))}
                className="w-full"
              />
            </div>
            <div>
              <label className="block text-xs text-zinc-400 mb-1.5">Max Tokens</label>
              <Input
                type="number"
                value={params.max_tokens}
                onChange={e => setParams(p => ({ ...p, max_tokens: parseInt(e.target.value) || 1024 }))}
              />
            </div>
          </CardBody>
        </Card>
      </div>
    </div>
  )
}
