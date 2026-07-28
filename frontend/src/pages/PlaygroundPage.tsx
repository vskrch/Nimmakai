import React, { useState, useRef, useEffect } from 'react'
import { Card, CardHeader, CardBody, Button, Input, Select, Textarea } from '../components/ui'
import { useCatalog } from '../hooks/useApi'
import { getAuthKey } from '../lib/api'
import {
  Send,
  Terminal,
  Sliders,
  Sparkles,
  User,
  Bot,
  Zap,
  RotateCcw
} from 'lucide-react'

interface Message {
  role: 'user' | 'assistant' | 'system'
  content: string
}

export default function PlaygroundPage() {
  const { data: catalog } = useCatalog()
  const [messages, setMessages] = useState<Message[]>([
    { role: 'assistant', content: "Hello! I am connected to the Potato Gateway. Type a prompt below to test automatic intent classification, model routing, and SSE response streaming." }
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
      let lineBuf = ''
      setMessages([...newMsgs, { role: 'assistant', content: '' }])

      if (reader) {
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          lineBuf += decoder.decode(value, { stream: true })
          const lines = lineBuf.split('\n')
          lineBuf = lines.pop() || ''
          for (const line of lines) {
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
            } catch { /* ignore incomplete json chunk */ }
          }
        }
      }
    } catch (e) {
      setMessages([...newMsgs, { role: 'assistant', content: `Network error: ${e}` }])
    }
    setStreaming(false)
  }

  function handleReset() {
    setMessages([
      { role: 'assistant', content: "Conversation reset. Enter a new test prompt." }
    ])
  }

  return (
    <div className="animate-[fadeIn_0.25s_ease-out] flex flex-col lg:flex-row gap-4 lg:gap-6 h-[calc(100dvh-140px)] lg:h-[calc(100vh-140px)]">
      {/* Interactive Chat Window */}
      <div className="flex-1 flex flex-col min-h-0 bg-zinc-900/60 backdrop-blur-xl border border-white/[0.08] rounded-2xl overflow-hidden shadow-[0_8px_32px_rgba(0,0,0,0.36)]">
        {/* Chat Window Header */}
        <div className="px-4 sm:px-6 py-3.5 border-b border-white/[0.08] flex justify-between items-center bg-white/[0.01] gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <Terminal className="w-4 h-4 text-violet-400 shrink-0" />
            <span className="text-xs font-bold text-white tracking-wide truncate">Interactive Session</span>
          </div>
          <Button size="xs" variant="default" onClick={handleReset} className="shrink-0">
            <RotateCcw className="w-3 h-3 text-zinc-400" />
            <span className="hidden sm:inline">Reset Chat</span>
          </Button>
        </div>

        {/* Message Stream */}
        <div ref={msgsRef} className="flex-1 overflow-y-auto p-4 sm:p-6 space-y-4 custom-scrollbar min-h-0">
          {messages.map((msg, i) => (
            <div key={i} className={`flex gap-3 max-w-[92%] sm:max-w-[85%] ${msg.role === 'user' ? 'ml-auto flex-row-reverse' : ''}`}>
              <div className={`w-8 h-8 rounded-xl shrink-0 flex items-center justify-center text-white ${
                msg.role === 'user'
                  ? 'bg-gradient-to-br from-violet-600 to-fuchsia-600 shadow-[0_0_12px_rgba(139,92,246,0.3)]'
                  : 'bg-zinc-800 border border-white/[0.1] text-violet-400'
              }`}>
                {msg.role === 'user' ? <User className="w-4 h-4" /> : <Bot className="w-4 h-4" />}
              </div>
              <div className={`p-4 rounded-2xl text-xs leading-relaxed whitespace-pre-wrap ${
                msg.role === 'user'
                  ? 'bg-violet-500/15 border border-violet-500/30 text-white font-medium shadow-[0_4px_16px_rgba(139,92,246,0.1)]'
                  : 'bg-zinc-950/80 border border-white/[0.08] text-zinc-200'
              }`}>
                {msg.content || (streaming && i === messages.length - 1 ? (
                  <span className="flex items-center gap-1.5 text-violet-400 font-mono">
                    <Zap className="w-3.5 h-3.5 animate-pulse" /> Generating stream...
                  </span>
                ) : '')}
              </div>
            </div>
          ))}
        </div>

        {/* Prompt Input Form */}
        <div className="p-3 sm:p-4 border-t border-white/[0.08] bg-zinc-950/80 flex gap-3 items-end">
          <Textarea
            rows={2}
            placeholder="Type a test prompt... (Enter to send, Shift+Enter for newline)"
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage() } }}
            className="flex-1 font-sans text-xs min-h-[44px]"
          />
          <Button variant="primary" onClick={sendMessage} disabled={streaming} className="h-10 px-4 sm:px-5 shrink-0">
            <Send className="w-4 h-4" />
            <span className="hidden sm:inline">{streaming ? '...' : 'Send'}</span>
          </Button>
        </div>
      </div>

      {/* Model Parameter Controls Panel */}
      <div className="w-full lg:w-[300px] flex flex-col gap-4 shrink-0 overflow-y-auto custom-scrollbar order-first lg:order-last">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Sliders className="w-4 h-4 text-violet-400" />
              <h3 className="text-sm font-semibold text-white">Execution Parameters</h3>
            </div>
          </CardHeader>
          <CardBody className="space-y-4 text-xs">
            <div>
              <label className="block text-xs font-semibold text-zinc-300 mb-1.5">Target Model</label>
              <Select
                value={model}
                onChange={e => setModel(e.target.value)}
              >
                <option value="auto">auto (Auto-Router Managed)</option>
                {allModels.slice(0, 25).map(m => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </Select>
            </div>
            <div>
              <div className="flex justify-between items-center mb-1.5">
                <label className="text-xs font-semibold text-zinc-300">Temperature</label>
                <span className="font-mono text-violet-300 font-bold">{params.temperature}</span>
              </div>
              <input
                type="range"
                min={0}
                max={2}
                step={0.1}
                value={params.temperature}
                onChange={e => setParams(p => ({ ...p, temperature: parseFloat(e.target.value) }))}
                className="w-full accent-violet-500 cursor-pointer"
              />
            </div>
            <div>
              <label className="block text-xs font-semibold text-zinc-300 mb-1.5">Max Tokens</label>
              <Input
                type="number"
                value={params.max_tokens}
                onChange={e => setParams(p => ({ ...p, max_tokens: parseInt(e.target.value) || 1024 }))}
              />
            </div>
          </CardBody>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center gap-2">
              <Sparkles className="w-4 h-4 text-violet-400" />
              <h3 className="text-sm font-semibold text-white">Routing Inspection</h3>
            </div>
          </CardHeader>
          <CardBody className="text-xs text-zinc-400 space-y-2">
            <p>When set to <code className="text-violet-300">auto</code>, the classifier inspects your prompt and selects the best model from the active ladder pool.</p>
          </CardBody>
        </Card>
      </div>
    </div>
  )
}
