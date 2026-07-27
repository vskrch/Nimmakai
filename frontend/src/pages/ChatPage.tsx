import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowUp, Check, ChevronDown, Copy, Edit3, History, Info, Loader2, Menu,
  Plus, Search, Send, Settings, Shield, ShieldOff, Square, Trash2, User,
  Wrench, X, Zap,
} from 'lucide-react'
import { clsx } from 'clsx'
import { Markdown } from '../lib/markdown'
import { getAuthKey, setAuthKey } from '../lib/api'
import { webSearch, looksLikeSearchQuery, type SearchResult } from '../lib/webSearch'

// ponytail: localStorage-only history. No server persistence. No retention when toggle off.

type Role = 'user' | 'assistant' | 'system'
type Modality = 'image' | 'audio' | 'video'

interface Attachment {
  id: string
  modality: Modality
  dataUrl: string  // base64 data URL
  name: string
  mime: string
  size: number
}

interface ChatMessage {
  role: Role
  content: string  // text content (string) OR rendered from parts
  model?: string
  ts?: number
  attachments?: Attachment[]  // multimodal inputs
  searchResults?: SearchResult[]  // web search citations for this turn
}
interface Conversation {
  id: string
  title: string
  messages: ChatMessage[]
  model: string
  createdAt: number
  updatedAt: number
}

const HISTORY_KEY = 'potato_chat_history_v1'
const SETTINGS_KEY = 'potato_chat_settings_v1'
const DEFAULT_MODEL = 'potato/auto'
const DEFAULT_CONTEXT = 256000
const MAX_CONVERSATIONS = 100

// Empty-state prompt suggestions — clickable chips that prefill the composer.
const SUGGESTIONS = [
  { label: 'Write a Python function', prompt: 'Write a Python function to compute the nth Fibonacci number using memoization', icon: '🐍' },
  { label: 'Explain a concept', prompt: 'Explain how transformer attention works, step by step', icon: '💡' },
  { label: 'Debug some code', prompt: 'Why does this throw a TypeError?\n\n```python\nx = [1, 2, 3]\nprint(x.sum())\n```', icon: '🐛' },
  { label: 'Draft an email', prompt: 'Draft a professional email to my team announcing a project kickoff meeting next Tuesday', icon: '✉' },
  { label: 'Brainstorm ideas', prompt: 'Brainstorm 5 creative product ideas that combine AI with everyday household tasks', icon: '✨' },
  { label: 'Compare options', prompt: 'Compare PostgreSQL vs MongoDB for a real-time analytics workload', icon: '⚖' },
]

interface ChatSettings {
  model: string
  temperature: number
  maxTokens: number
  keepHistory: boolean
  systemPrompt: string
  // advanced (gear icon)
  topP: number
  frequencyPenalty: number
  presencePenalty: number
  stop: string
  stream: boolean
  // model picker scope: false (default) = only potato/* router virtuals;
  // true = also list every upstream model in the pool.
  showAllModels: boolean
}

const DEFAULT_SETTINGS: ChatSettings = {
  model: DEFAULT_MODEL,
  temperature: 0.7,
  maxTokens: 4096,
  keepHistory: true,
  systemPrompt: '',
  topP: 1.0,
  frequencyPenalty: 0.0,
  presencePenalty: 0.0,
  stop: '',
  stream: true,
  showAllModels: false,
}

interface ModelInfo {
  id: string
  owned_by?: string
  context_length?: number
  max_model_len?: number
  max_output_tokens?: number
  supports_tools?: boolean
  supports_vision?: boolean
  description?: string
  object?: string
  created?: number
  root?: string
  parent?: string | null
  permission?: unknown[]
}

interface ModelScore {
  model: string
  score?: number
  intelligence?: number
  speed?: number
  health?: number
  unhealthy?: boolean
}

// ---------- persistence ----------
function loadSettings(): ChatSettings {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY)
    if (!raw) return DEFAULT_SETTINGS
    return { ...DEFAULT_SETTINGS, ...JSON.parse(raw) }
  } catch {
    return DEFAULT_SETTINGS
  }
}
function saveSettings(s: ChatSettings) {
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(s)) } catch { /* ignore */ }
}
function loadConversations(): Conversation[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY)
    if (!raw) return []
    const arr = JSON.parse(raw)
    return Array.isArray(arr) ? arr : []
  } catch { return [] }
}
function saveConversations(convs: Conversation[]) {
  try {
    // Cap to last MAX_CONVERSATIONS by updatedAt (most recent kept). Older chats
    // are dropped silently — localStorage is bounded and this prevents quota errors.
    const sorted = [...convs].sort((a, b) => b.updatedAt - a.updatedAt)
    const trimmed = sorted.slice(0, MAX_CONVERSATIONS)
    localStorage.setItem(HISTORY_KEY, JSON.stringify(trimmed))
  } catch { /* quota */ }
}
function clearConversations() {
  try { localStorage.removeItem(HISTORY_KEY) } catch { /* ignore */ }
}

function uid() {
  return `c_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`
}
function titleFrom(text: string): string {
  const clean = text.replace(/\s+/g, ' ').trim()
  return clean.length > 40 ? clean.slice(0, 40) + '…' : clean || 'New chat'
}

// ---------- file handling ----------
const MAX_FILE_MB = 20
const ACCEPTED = {
  image: ['image/png', 'image/jpeg', 'image/webp', 'image/gif'],
  audio: ['audio/wav', 'audio/mp3', 'audio/mpeg', 'audio/ogg', 'audio/webm'],
  video: ['video/mp4', 'video/webm', 'video/ogg'],
}

function detectModality(mime: string): Modality | null {
  if (ACCEPTED.image.includes(mime)) return 'image'
  if (ACCEPTED.audio.includes(mime)) return 'audio'
  if (ACCEPTED.video.includes(mime)) return 'video'
  return null
}

function fileToDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(r.result as string)
    r.onerror = () => reject(r.error)
    r.readAsDataURL(file)
  })
}

async function filesToAttachments(files: FileList | File[]): Promise<Attachment[]> {
  const out: Attachment[] = []
  for (const f of Array.from(files)) {
    const modality = detectModality(f.type)
    if (!modality) continue
    if (f.size > MAX_FILE_MB * 1024 * 1024) continue
    try {
      const dataUrl = await fileToDataUrl(f)
      out.push({
        id: `a_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`,
        modality,
        dataUrl,
        name: f.name,
        mime: f.type,
        size: f.size,
      })
    } catch { /* ignore unreadable */ }
  }
  return out
}

// Build the system context message from web search results
function buildSearchContext(query: string, results: SearchResult[]): string {
  const parts = results.map((r, i) =>
    `[${i + 1}] ${r.title}\n${r.snippet}\nSource: ${r.url}`,
  )
  return `Web search results for "${query}":\n\n${parts.join('\n\n')}\n\nYou have been given web search results above. Use ONLY these results to answer the user's question. Cite sources as [1], [2], etc. Do NOT attempt to call any tools or functions (e.g. search_web) — the search has already been performed for you. If the results do not answer the question, say so plainly.`
}

// ---------- API ----------
// ponytail: use authenticated /v1/* when a key is present, else fall back to
// public /chat/api/* (no account needed — gateway serves itself).
function chatBase(): string {
  return getAuthKey() ? '/v1' : '/chat/api'
}

async function fetchModels(): Promise<ModelInfo[]> {
  const key = getAuthKey()
  const base = chatBase()
  const res = await fetch(`${base}/models`, {
    headers: key ? { Authorization: `Bearer ${key}` } : {},
  })
  if (!res.ok) return []
  const body = await res.json()
  const data: ModelInfo[] = body.data || []
  return data
}
// ponytail: best-effort quality scores. Admin-only endpoint; silently empty for users.
const scoreCache = new Map<string, ModelScore>()
async function fetchScores(): Promise<Map<string, ModelScore>> {
  if (scoreCache.size) return scoreCache
  try {
    const key = getAuthKey()
    const res = await fetch('/admin/rankings', {
      headers: key ? { Authorization: `Bearer ${key}` } : {},
    })
    if (!res.ok) return scoreCache
    const body = await res.json()
    const breakdown: ModelScore[] = body.score_breakdown || []
    for (const s of breakdown) scoreCache.set(s.model, s)
  } catch { /* non-admin or unavailable */ }
  return scoreCache
}

// Catalog (dynamic chains / best models) — admin-only; gracefully empty
interface CatalogData {
  best_coding?: string[]
  best_chat?: string[]
  best_reasoning?: string[]
  dynamic_chains?: Record<string, string[]>
  intents?: Record<string, { description?: string; chain?: string[]; primary_family?: string }>
}
let catalogData: CatalogData = {}
async function fetchCatalog(): Promise<void> {
  try {
    const key = getAuthKey()
    const res = await fetch('/catalog', {
      headers: key ? { Authorization: `Bearer ${key}` } : {},
    })
    if (!res.ok) return
    catalogData = await res.json()
  } catch { /* non-admin */ }
}

// Virtual router tier metadata — what each potato/* auto-router does
const TIER_INFO: Record<string, { title: string; desc: string; useCase: string; chains: string[] }> = {
  'potato/auto': {
    title: 'Potato Auto (Balanced)',
    desc: 'Default intelligent router. Analyzes your prompt and picks the best model from all enabled providers, balancing quality × speed × availability.',
    useCase: 'General chat, code, analysis — use when unsure.',
    chains: ['coding_agentic', 'chat_fast', 'reasoning'],
  },
  'auto': {
    title: 'Auto (Balanced)',
    desc: 'Alias of potato/auto. Analyzes intent and picks the strongest available model across all providers.',
    useCase: 'Default for any task.',
    chains: ['coding_agentic', 'chat_fast', 'reasoning'],
  },
  'potato/auto-coding': {
    title: 'Potato Auto Coding',
    desc: 'Forces the coding/agentic ladder. Picks the strongest tool-capable model for multi-file edits, refactors, and agent harnesses.',
    useCase: 'Cursor / Cline / agentic coding, tool calls, multi-turn refactors.',
    chains: ['coding_agentic'],
  },
  'potato/coding': {
    title: 'Potato Coding',
    desc: 'Same as potato/auto-coding. Forces the coding/agentic ladder with the strongest tool-capable model.',
    useCase: 'Agentic coding, tool execution, multi-file refactors.',
    chains: ['coding_agentic'],
  },
  'potato/best': {
    title: 'Potato Best (Frontier)',
    desc: 'Frontier reasoning tier. Picks the highest-capability model available — for math, proofs, deep multi-step reasoning.',
    useCase: 'Hard math, theorems, complex multi-step reasoning.',
    chains: ['reasoning', 'long_horizon'],
  },
  'potato/auto-fast': {
    title: 'Potato Auto Fast',
    desc: 'Latency-first tier. Picks the model with the lowest TTFT and highest TPS for fast responses.',
    useCase: 'Quick Q&A, summaries, short chats where speed matters.',
    chains: ['chat_fast'],
  },
  'potato/auto-cheap': {
    title: 'Potato Auto Cheap (Efficient)',
    desc: 'Cost-aware tier. Picks lightweight high-efficiency models that get the job done at lowest token cost.',
    useCase: 'High-volume, low-complexity workloads.',
    chains: ['chat_fast'],
  },
  'openrouter/auto': {
    title: 'OpenRouter Auto',
    desc: 'OpenRouter-compatible alias. Same as potato/auto — balanced quality × speed across all providers.',
    useCase: 'Drop-in for OpenRouter clients.',
    chains: ['coding_agentic', 'chat_fast'],
  },
  'kilo/auto': {
    title: 'Kilo Auto (Balanced)',
    desc: 'Kilo-compatible alias. Balanced quality × speed across all providers.',
    useCase: 'Drop-in for Kilo clients.',
    chains: ['coding_agentic', 'chat_fast'],
  },
  'kilo-auto/frontier': {
    title: 'Kilo Auto Frontier',
    desc: 'Kilo-compatible frontier tier. Highest-capability model available.',
    useCase: 'Hardest reasoning tasks.',
    chains: ['reasoning', 'long_horizon'],
  },
  'kilo-auto/balanced': {
    title: 'Kilo Auto Balanced',
    desc: 'Kilo-compatible balanced tier. Quality × speed across all providers.',
    useCase: 'General purpose.',
    chains: ['coding_agentic', 'chat_fast'],
  },
  'kilo-auto/efficient': {
    title: 'Kilo Auto Efficient',
    desc: 'Kilo-compatible cost-aware tier. Lightweight capable models.',
    useCase: 'Cost-sensitive workloads.',
    chains: ['chat_fast'],
  },
  'kilo-auto/free': {
    title: 'Kilo Auto Free',
    desc: 'Kilo-compatible free-only pool. Restricts to free-tier providers (Groq, Cerebras, OpenCode Zen, etc.).',
    useCase: 'Zero-cost routing.',
    chains: ['chat_fast'],
  },
}

function getTierInfo(modelId: string) {
  return TIER_INFO[modelId]
}

function getChainModels(modelId: string): string[] {
  const info = getTierInfo(modelId)
  if (!info) return []
  const out: string[] = []
  for (const intent of info.chains) {
    let chain: string[] = []
    if (catalogData.dynamic_chains?.[intent]) {
      chain = catalogData.dynamic_chains[intent]
    } else if (intent === 'coding_agentic' && catalogData.best_coding) {
      chain = catalogData.best_coding
    } else if (intent === 'chat_fast' && catalogData.best_chat) {
      chain = catalogData.best_chat
    } else if (intent === 'reasoning' && catalogData.best_reasoning) {
      chain = catalogData.best_reasoning
    } else if (catalogData.intents?.[intent]?.chain) {
      chain = catalogData.intents[intent].chain!
    }
    for (const m of chain) if (!out.includes(m)) out.push(m)
  }
  return out.slice(0, 8)
}

// Build OpenAI-compatible content for a message: string for text-only, array
// of parts when multimodal attachments are present.
function buildMessageContent(m: ChatMessage): string | unknown[] {
  if (!m.attachments?.length) return m.content
  const parts: unknown[] = []
  if (m.content) parts.push({ type: 'text', text: m.content })
  for (const att of m.attachments) {
    if (att.modality === 'image') {
      parts.push({ type: 'image_url', image_url: { url: att.dataUrl } })
    } else if (att.modality === 'audio') {
      // OpenAI audio format: { type: 'input_audio', input_audio: { data, format } }
      const fmt = att.mime.split('/')[1] || 'wav'
      const b64 = att.dataUrl.split(',')[1] || ''
      parts.push({ type: 'input_audio', input_audio: { data: b64, format: fmt } })
    } else if (att.modality === 'video') {
      // Video: send as image_url with data URL (providers that accept video
      // via the image_url field, e.g. Gemini via OpenAI compat). Fallback:
      // most VLMs will reject; the router moves to the next vision model.
      parts.push({ type: 'image_url', image_url: { url: att.dataUrl } })
    }
  }
  return parts
}

async function* streamChat(
  messages: ChatMessage[],
  model: string,
  settings: ChatSettings,
  signal: AbortSignal,
): AsyncGenerator<string> {
  const key = getAuthKey()
  const base = chatBase()
  const body: Record<string, unknown> = {
    model,
    messages: messages.map(m => ({ role: m.role, content: buildMessageContent(m) })),
    stream: settings.stream,
    temperature: settings.temperature,
    max_tokens: settings.maxTokens,
    // The chat UI never sends tools, so tell the upstream explicitly not to
    // emit tool calls. Without this, tool-capable models (Qwen, GLM, etc.)
    // hallucinate raw tool-call tokens like <|tool_calls_section_begin|>
    // into the content stream, which the UI renders as garbage text.
    tool_choice: 'none',
  }
  if (settings.topP !== 1.0) body.top_p = settings.topP
  if (settings.frequencyPenalty !== 0) body.frequency_penalty = settings.frequencyPenalty
  if (settings.presencePenalty !== 0) body.presence_penalty = settings.presencePenalty
  if (settings.stop.trim()) body.stop = settings.stop.split(',').map(s => s.trim()).filter(Boolean)
  const res = await fetch(`${base}/chat/completions`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(key ? { Authorization: `Bearer ${key}` } : {}),
    },
    body: JSON.stringify(body),
    signal,
  })
  if (!res.ok) {
    const txt = await res.text()
    throw new Error(`HTTP ${res.status}: ${txt}`)
  }
  if (!settings.stream) {
    const data = await res.json()
    const content = data.choices?.[0]?.message?.content || ''
    if (content) yield stripToolTokens(content)
    return
  }
  if (!res.body) throw new Error('No response body')
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const lines = buf.split('\n')
    buf = lines.pop() || ''
    for (const line of lines) {
      const t = line.trim()
      if (!t.startsWith('data: ')) continue
      const data = t.slice(6)
      if (data === '[DONE]') return
      try {
        const parsed = JSON.parse(data)
        const delta = parsed.choices?.[0]?.delta?.content
        if (delta) yield stripToolTokens(delta)
      } catch { /* ignore */ }
    }
  }
}

// Safety net: some upstreams ignore tool_choice:'none' and emit raw
// tool-call token patterns (Qwen's ChatML <│tool_calls_section_begin│>,
// GLM's function-call markers, generic <│tool▁calls▁begin│>, etc.) into
// the content stream. Strip them so the chat UI never renders garbage.
// ponytail: regex-based scrub; the gateway also normalizes but this guards
// against models that emit tool tokens inside delta.content directly.
const TOOL_TOKEN_RE = /<\|tool_calls_section_begin\|>|<\|tool_calls_section_end\|>|<\|tool_call_begin\|>|<\|tool_call_end\|>|<\|tool▁calls▁begin\|>|<\|tool▁calls▁end\|>|<\|tool▁call▁begin\|>|<\|tool▁call▁end\|>|<\|im_start\|>|<\|im_end\|>/g

// Reasoning blocks: many open models (DeepSeek-R1, Qwen-QwQ, GLM-Zero,
// Nemotron, etc.) stream their internal chain-of-thought wrapped in
// <think>...</think> (or <thinking>...</thinking>) inside delta.content.
// The chat UI must show only the final answer, not the model's scratchpad.
// Strip completed blocks and trailing open blocks (the answer follows).
const THINK_BLOCK_RE = /<think(?:ing)?>[\s\S]*?<\/think(?:ing)?>|<think(?:ing)?>[\s\S]*$/g

function stripToolTokens(text: string): string {
  if (!text) return text
  let out = text
  if (out.includes('<think') || out.includes('<Thinking') || out.includes('<THINK')) {
    out = out.replace(THINK_BLOCK_RE, '')
  }
  if (out.includes('<│') || out.includes('<|')) {
    out = out.replace(TOOL_TOKEN_RE, '')
  }
  return out
}

// ---------- icons ----------
function RoleAvatar({ role, model }: { role: Role; model?: string }) {
  if (role === 'user') {
    return (
      <div className="w-8 h-8 rounded-full bg-gradient-to-br from-violet-600 to-fuchsia-600 flex items-center justify-center shadow-[0_0_12px_rgba(139,92,246,0.3)] shrink-0">
        <User className="w-4 h-4 text-white" />
      </div>
    )
  }
  return (
    <div className="w-8 h-8 rounded-full bg-zinc-900 border border-white/[0.1] flex items-center justify-center shrink-0">
      <Zap className="w-4 h-4 text-violet-400 fill-white" />
    </div>
  )
}

function AttachmentPreview({ att }: { att: Attachment }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="rounded-xl border border-white/[0.1] bg-zinc-950/60 overflow-hidden hover:border-violet-500/40 transition-all"
        title={att.name}
      >
        {att.modality === 'image' && (
          <img src={att.dataUrl} alt={att.name} className="w-20 h-20 object-cover" />
        )}
        {att.modality === 'audio' && (
          <div className="w-20 h-20 flex flex-col items-center justify-center gap-1 text-zinc-300 bg-gradient-to-br from-zinc-900 to-zinc-800">
            <span className="text-[9px] uppercase tracking-wider text-violet-300">{att.mime.split('/')[1]}</span>
            <span className="text-[8px] text-zinc-500">audio</span>
          </div>
        )}
        {att.modality === 'video' && (
          <video src={att.dataUrl} className="w-20 h-20 object-cover" muted />
        )}
      </button>
      {open && (
        <div
          className="fixed inset-0 bg-black/80 backdrop-blur-md z-50 flex items-center justify-center p-6"
          onClick={() => setOpen(false)}
        >
          <button className="absolute top-4 right-4 p-2 text-zinc-400 hover:text-white" onClick={() => setOpen(false)}>
            <X className="w-6 h-6" />
          </button>
          {att.modality === 'image' && (
            <img src={att.dataUrl} alt={att.name} className="max-w-full max-h-full rounded-xl" />
          )}
          {att.modality === 'audio' && (
            <div className="bg-zinc-900 border border-white/10 rounded-2xl p-6 max-w-md w-full">
              <div className="text-sm font-semibold text-white mb-3">{att.name}</div>
              <audio src={att.dataUrl} controls className="w-full" />
            </div>
          )}
          {att.modality === 'video' && (
            <video src={att.dataUrl} controls className="max-w-full max-h-full rounded-xl" />
          )}
        </div>
      )}
    </>
  )
}

// ---------- main ----------
export default function ChatPage({ embedded = false }: { embedded?: boolean }) {
  const [settings, setSettings] = useState<ChatSettings>(() => loadSettings())
  const [conversations, setConversations] = useState<Conversation[]>(() => loadConversations())
  const [activeId, setActiveId] = useState<string | null>(null)
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const [models, setModels] = useState<ModelInfo[]>([])
  const [showModels, setShowModels] = useState(false)
  const [showSettings, setShowSettings] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [showSidebar, setShowSidebar] = useState(false)
  const [editError, setEditError] = useState<string | null>(null)
  const [attachments, setAttachments] = useState<Attachment[]>([])
  const [dragOver, setDragOver] = useState(false)
  const [webSearchEnabled, setWebSearchEnabled] = useState(true)
  const [searching, setSearching] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  // initial load
  useEffect(() => {
    fetchModels().then(setModels).catch(() => {})
    fetchScores().catch(() => {})
    fetchCatalog().catch(() => {})
    if (conversations.length) {
      setActiveId(conversations[0].id)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // persist
  useEffect(() => { saveSettings(settings) }, [settings])
  useEffect(() => {
    if (settings.keepHistory) saveConversations(conversations)
  }, [conversations, settings.keepHistory])

  // auto-scroll
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  })

  // collapse dropdown on outside click
  useEffect(() => {
    if (!showModels) return
    const h = () => setShowModels(false)
    window.addEventListener('click', h)
    return () => window.removeEventListener('click', h)
  }, [showModels])

  const active = useMemo(
    () => conversations.find(c => c.id === activeId) || null,
    [conversations, activeId],
  )

  // ---------- chat actions ----------
  const newChat = useCallback(() => {
    setActiveId(null)
    setInput('')
    setAttachments([])
    setEditError(null)
    inputRef.current?.focus()
  }, [])

  // keyboard shortcuts: Cmd/Ctrl+K = new chat, Cmd/Ctrl+/ = focus model picker
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const mod = e.metaKey || e.ctrlKey
      if (!mod) return
      if (e.key === 'k' || e.key === 'K') {
        e.preventDefault()
        newChat()
      } else if (e.key === '/') {
        e.preventDefault()
        setShowModels(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [newChat])

  const deleteConv = useCallback((id: string) => {
    setConversations(prev => prev.filter(c => c.id !== id))
    if (activeId === id) setActiveId(null)
  }, [activeId])

  const selectConv = useCallback((id: string) => {
    setActiveId(id)
    setShowSidebar(false)
    setEditError(null)
  }, [])

  const clearAllHistory = useCallback(() => {
    if (!confirm('Delete all chat history? This cannot be undone.')) return
    setConversations([])
    clearConversations()
    setActiveId(null)
  }, [])

  const toggleKeepHistory = useCallback(() => {
    setSettings(s => {
      const next = { ...s, keepHistory: !s.keepHistory }
      if (!next.keepHistory) {
        clearConversations()
        setConversations([])
        setActiveId(null)
      }
      return next
    })
  }, [])

  // ---------- send ----------
  const send = useCallback(async () => {
    if ((!input.trim() && !attachments.length) || streaming) return
    // ponytail: no auth key required — public /chat/api/* serves anonymous users.
    const userText = input.trim()
    const userMsg: ChatMessage = {
      role: 'user',
      content: userText,
      ts: Date.now(),
      attachments: attachments.length ? attachments : undefined,
    }
    const model = settings.model
    let convMessages: ChatMessage[] = active ? [...active.messages, userMsg] : [userMsg]
    const convId = active?.id || uid()
    const title = active?.title || titleFrom(userMsg.content || attachments[0]?.name || 'New chat')
    const now = Date.now()

    // Web search (browser-side, uses user's IP) when enabled + heuristic match
    let searchResults: SearchResult[] | undefined
    if (webSearchEnabled && userText && looksLikeSearchQuery(userText) && !attachments.length) {
      setSearching(true)
      try {
        const sr = await webSearch(userText)
        if (sr.results.length) {
          searchResults = sr.results
          userMsg.searchResults = searchResults
          // Rebuild convMessages with the updated userMsg (now has searchResults)
          convMessages = active ? [...active.messages, userMsg] : [userMsg]
        }
      } catch { /* search failed — continue without context */ }
      setSearching(false)
    }

    // commit the user message immediately
    setConversations(prev => {
      if (active) {
        return prev.map(c => c.id === convId
          ? { ...c, messages: convMessages, updatedAt: now }
          : c,
        )
      }
      return [{
        id: convId,
        title,
        messages: convMessages,
        model,
        createdAt: now,
        updatedAt: now,
      }, ...prev]
    })
    setActiveId(convId)
    setInput('')
    setAttachments([])
    setStreaming(true)
    setEditError(null)

    const ac = new AbortController()
    abortRef.current = ac

    // build payload — optionally prepend web search context as a system message
    const payloadMessages: ChatMessage[] = []
    if (settings.systemPrompt.trim()) {
      payloadMessages.push({ role: 'system', content: settings.systemPrompt.trim() })
    }
    if (searchResults?.length) {
      const ctx = buildSearchContext(userText, searchResults)
      payloadMessages.push({ role: 'system', content: ctx })
    }
    payloadMessages.push(...convMessages)

    let assistantContent = ''
    // append empty assistant message
    setConversations(prev => prev.map(c => c.id === convId
      ? { ...c, messages: [...convMessages, { role: 'assistant', content: '', model, ts: Date.now() }] }
      : c,
    ))

    try {
      for await (const delta of streamChat(payloadMessages, model, settings, ac.signal)) {
        assistantContent += delta
        setConversations(prev => prev.map(c => c.id === convId
          ? {
            ...c,
            messages: [...convMessages, {
              role: 'assistant', content: assistantContent, model, ts: Date.now(),
            }],
            updatedAt: Date.now(),
          }
          : c,
        ))
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      if (ac.signal.aborted) {
        // keep partial content
        if (assistantContent) {
          setConversations(prev => prev.map(c => c.id === convId
            ? {
              ...c,
              messages: [...convMessages, {
                role: 'assistant', content: assistantContent + '\n\n_⏹ stopped_', model, ts: Date.now(),
              }],
            }
            : c,
          ))
        } else {
          // remove the empty assistant placeholder
          setConversations(prev => prev.map(c => c.id === convId
            ? { ...c, messages: convMessages }
            : c,
          ))
        }
      } else {
        setEditError(msg)
        setConversations(prev => prev.map(c => c.id === convId
          ? {
            ...c,
            messages: [...convMessages, {
              role: 'assistant',
              content: `⚠️ Error: ${msg}`,
              model,
              ts: Date.now(),
            }],
          }
          : c,
        ))
      }
    } finally {
      setStreaming(false)
      abortRef.current = null
      inputRef.current?.focus()
    }
  }, [input, streaming, active, settings, models])

  const stop = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  // ---------- model picker ----------
  const selectedModel = useMemo(() => models.find(m => m.id === settings.model), [models, settings.model])
  const modelContext = selectedModel
    ? (selectedModel.context_length || selectedModel.max_model_len || DEFAULT_CONTEXT)
    : DEFAULT_CONTEXT

  // group models: virtuals first, then by provider. When showAllModels is
  // off (default), only list potato/* router virtuals so the picker stays
  // clean — flip the toggle in Settings to expose every upstream model.
  const grouped = useMemo(() => {
    const virtuals: ModelInfo[] = []
    const providers: Record<string, ModelInfo[]> = {}
    for (const m of models) {
      const owner = m.owned_by || (m.id.includes('/') ? m.id.split('/')[0] : 'unknown')
      const isVirtual = m.id === 'potato/auto' || m.id.startsWith('potato/auto') || m.id === 'auto'
        || m.id.startsWith('openrouter/') || m.id.startsWith('kilo')
      if (isVirtual) {
        virtuals.push(m)
      } else if (settings.showAllModels) {
        (providers[owner] ||= []).push(m)
      }
    }
    return { virtuals, providers: Object.entries(providers).sort((a, b) => a[0].localeCompare(b[0])) }
  }, [models, settings.showAllModels])

  const layout = embedded
    ? 'absolute inset-0 z-30 bg-zinc-950'
    : 'fixed inset-0 bg-zinc-950'

  return (
    <div className={clsx(layout, 'text-zinc-100 font-sans antialiased flex')}>
      {/* Sidebar */}
      <aside className={clsx(
        'absolute md:relative z-20 h-full w-[260px] bg-zinc-950 border-r border-white/[0.08] flex flex-col transition-transform duration-200',
        showSidebar ? 'translate-x-0' : '-translate-x-full md:translate-x-0',
      )}>
        <div className="p-3 flex items-center justify-between border-b border-white/[0.06]">
          <button
            onClick={newChat}
            className="flex-1 flex items-center gap-2 px-3 py-2.5 rounded-xl bg-zinc-900 border border-white/[0.1] text-[13px] font-medium hover:bg-zinc-800 hover:border-white/20 transition-all"
          >
            <Plus className="w-4 h-4" /> New chat
          </button>
          <button
            onClick={() => setShowSidebar(false)}
            className="md:hidden p-2 text-zinc-400 hover:text-white"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto custom-scrollbar px-2 py-2 space-y-0.5">
          {settings.keepHistory && conversations.map(c => (
            <div
              key={c.id}
              onClick={() => selectConv(c.id)}
              className={clsx(
                'group flex items-center gap-2 px-3 py-2.5 rounded-xl text-[13px] cursor-pointer transition-all',
                c.id === activeId ? 'bg-violet-500/15 text-violet-200 border border-violet-500/30' : 'text-zinc-400 hover:bg-white/[0.04] hover:text-zinc-200 border border-transparent',
              )}
            >
              <History className="w-3.5 h-3.5 shrink-0 opacity-60" />
              <span className="flex-1 truncate">{c.title}</span>
              <button
                onClick={e => { e.stopPropagation(); deleteConv(c.id) }}
                className="opacity-0 group-hover:opacity-100 p-1 rounded-md text-zinc-500 hover:text-rose-400 hover:bg-rose-500/10 transition-all"
                title="Delete"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          ))}
          {!settings.keepHistory && (
            <div className="px-3 py-8 text-center">
              <ShieldOff className="w-8 h-8 text-zinc-700 mx-auto mb-2" />
              <p className="text-[11px] text-zinc-500 leading-relaxed">
                No-retention mode.<br />History is kept only in this browser tab.
              </p>
            </div>
          )}
        </div>

        <div className="p-3 border-t border-white/[0.08] space-y-2">
          <button
            onClick={() => setShowSettings(true)}
            className="w-full flex items-center gap-2 px-3 py-2 rounded-xl text-[12px] text-zinc-300 hover:bg-white/[0.04] transition-all"
          >
            <Settings className="w-3.5 h-3.5" /> Settings
          </button>
          {settings.keepHistory && conversations.length > 0 && (
            <button
              onClick={clearAllHistory}
              className="w-full flex items-center gap-2 px-3 py-2 rounded-xl text-[12px] text-rose-300 hover:bg-rose-500/10 transition-all"
            >
              <Trash2 className="w-3.5 h-3.5" /> Clear all history
            </button>
          )}
        </div>
      </aside>

      {/* backdrop for mobile */}
      {showSidebar && (
        <div
          className="md:hidden absolute inset-0 bg-black/60 z-10"
          onClick={() => setShowSidebar(false)}
        />
      )}

      {/* Main chat area */}
      <div className="flex-1 flex flex-col min-w-0 relative">
        {/* Top bar */}
        <header className="h-14 px-4 flex items-center justify-between border-b border-white/[0.08] bg-zinc-950/80 backdrop-blur-xl shrink-0">
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowSidebar(s => !s)}
              className="md:hidden p-2 text-zinc-400 hover:text-white"
            >
              <Menu className="w-5 h-5" />
            </button>
            <div className="flex items-center gap-2">
              <div className="w-7 h-7 bg-gradient-to-br from-violet-600 to-fuchsia-600 rounded-lg flex items-center justify-center shadow-[0_0_12px_rgba(139,92,246,0.4)]">
                <Zap className="w-4 h-4 text-white fill-white" />
              </div>
              <span className="text-sm font-semibold text-white">Potato Chat</span>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={toggleKeepHistory}
              title={settings.keepHistory ? 'History on (saved in browser)' : 'No retention (this tab only)'}
              className={clsx(
                'flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-medium border transition-all',
                settings.keepHistory
                  ? 'bg-emerald-500/10 text-emerald-300 border-emerald-500/20 hover:bg-emerald-500/15'
                  : 'bg-amber-500/10 text-amber-300 border-amber-500/25 hover:bg-amber-500/15',
              )}
            >
              {settings.keepHistory ? <Shield className="w-3.5 h-3.5" /> : <ShieldOff className="w-3.5 h-3.5" />}
              {settings.keepHistory ? 'History' : 'No retention'}
            </button>

            <button
              onClick={() => setShowSettings(true)}
              className="p-2 rounded-lg text-zinc-400 hover:text-white hover:bg-white/[0.05] transition-all"
              title="Settings"
            >
              <Settings className="w-4 h-4" />
            </button>
          </div>
        </header>

        {/* Messages */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto custom-scrollbar">
          {!active && (
            <div className="h-full flex flex-col items-center justify-center px-6 text-center">
              <div className="w-16 h-16 bg-gradient-to-br from-violet-600 to-fuchsia-600 rounded-3xl flex items-center justify-center shadow-[0_0_32px_rgba(139,92,246,0.4)] mb-5 border border-white/20">
                <Zap className="w-8 h-8 text-white fill-white" />
              </div>
              <h1 className="text-2xl font-bold text-white mb-2">How can I help today?</h1>
              <p className="text-[13px] text-zinc-400 max-w-md mb-6">
                Chat with {models.length || 'your'} available models through the Potato Gateway.
                {!getAuthKey() && (
                  <span className="block mt-1 text-emerald-300">
                    Public mode — no account required.
                  </span>
                )}
                <span className="block mt-1 text-zinc-500">
                  Supports text, images, audio, and video inputs.
                </span>
              </p>
              {/* Prompt suggestion chips */}
              <div className="flex flex-wrap gap-2 justify-center max-w-lg">
                {SUGGESTIONS.map(s => (
                  <button
                    key={s.label}
                    onClick={() => { setInput(s.prompt); inputRef.current?.focus() }}
                    className="px-3 py-2 rounded-xl bg-zinc-900/80 border border-white/[0.08] text-[12px] text-zinc-300 hover:border-violet-500/40 hover:bg-violet-500/5 hover:text-violet-200 transition-all group"
                  >
                    <span className="text-violet-400/70 group-hover:text-violet-300 mr-1.5">{s.icon}</span>
                    {s.label}
                  </button>
                ))}
              </div>
              {!settings.keepHistory && (
                <p className="mt-6 inline-flex items-center gap-1.5 text-[11px] text-amber-300">
                  <ShieldOff className="w-3 h-3" /> No-retention mode — history never leaves this tab.
                </p>
              )}
            </div>
          )}

          {active && (
            <div className="max-w-3xl mx-auto px-4 py-6 space-y-6">
              {active.messages.map((m, i) => (
                <div key={i} className="group flex gap-3">
                  <RoleAvatar role={m.role} model={m.model} />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-[11px] font-semibold text-zinc-300">
                        {m.role === 'user' ? 'You' : 'Potato'}
                      </span>
                      {m.model && m.role === 'assistant' && (
                        <span className="text-[10px] text-zinc-500 font-mono">{m.model}</span>
                      )}
                      {m.content && (
                        <button
                          onClick={() => navigator.clipboard.writeText(m.content)}
                          className="opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded-md text-zinc-500 hover:text-zinc-200 hover:bg-white/[0.05]"
                          title="Copy"
                        >
                          <Copy className="w-3 h-3" />
                        </button>
                      )}
                    </div>
                    {m.attachments && m.attachments.length > 0 && (
                      <div className="flex flex-wrap gap-2 mb-2">
                        {m.attachments.map(att => (
                          <AttachmentPreview key={att.id} att={att} />
                        ))}
                      </div>
                    )}
                    {m.searchResults && m.searchResults.length > 0 && (
                      <div className="mb-2 p-2.5 rounded-xl bg-emerald-500/5 border border-emerald-500/20">
                        <div className="flex items-center gap-1.5 text-[10px] font-semibold text-emerald-300 uppercase tracking-wider mb-1.5">
                          <Search className="w-3 h-3" /> Web sources
                        </div>
                        <div className="space-y-1">
                          {m.searchResults.map((r, ri) => (
                            <a
                              key={ri}
                              href={r.url}
                              target="_blank"
                              rel="noreferrer"
                              className="flex items-start gap-2 text-[11px] text-zinc-300 hover:text-white transition-colors group"
                            >
                              <span className="text-emerald-400 font-mono shrink-0">[{ri + 1}]</span>
                              <span className="min-w-0">
                                <span className="font-medium text-zinc-200 group-hover:text-white truncate block">{r.title}</span>
                                <span className="text-zinc-500 truncate block">{r.url}</span>
                              </span>
                            </a>
                          ))}
                        </div>
                      </div>
                    )}
                    {m.content
                      ? <Markdown content={m.content} />
                      : streaming && i === active.messages.length - 1
                        ? (
                          <div className="flex items-center gap-2 text-violet-400 text-[13px]">
                            <Loader2 className="w-4 h-4 animate-spin" /> Thinking…
                          </div>
                        )
                        : null
                    }
                  </div>
                </div>
              ))}
              {streaming && active.messages[active.messages.length - 1]?.content && (
                <div className="flex items-center gap-3 pl-11 pt-1">
                  <div className="flex items-center gap-1.5 text-violet-400 text-[11px] font-mono">
                    <span className="w-1.5 h-1.5 bg-violet-400 rounded-full animate-pulse" /> generating
                  </div>
                  <button
                    onClick={stop}
                    className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-rose-500/10 border border-rose-500/20 text-rose-300 hover:bg-rose-500/15 text-[11px] font-medium transition-all"
                    title="Stop generating"
                  >
                    <Square className="w-3 h-3 fill-current" />
                    Stop
                  </button>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Input */}
        <div className="px-4 pb-4 pt-2 shrink-0 bg-zinc-950">
          <div className="max-w-3xl mx-auto">
            {editError && (
              <div className="mb-2 px-3 py-2 rounded-xl bg-rose-500/10 border border-rose-500/20 text-[11px] text-rose-300 flex items-center gap-2">
                <X className="w-3.5 h-3.5 shrink-0" />
                <span className="flex-1 truncate">{editError}</span>
                <button onClick={() => setEditError(null)} className="text-rose-400 hover:text-rose-200">
                  <X className="w-3.5 h-3.5" />
                </button>
              </div>
            )}

            {/* Model picker */}
            <div className="mb-2 flex items-center gap-2">
              <div className="relative">
                <button
                  onClick={e => { e.stopPropagation(); setShowModels(s => !s) }}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-zinc-900 border border-white/[0.1] text-[12px] font-medium text-zinc-200 hover:bg-zinc-800 hover:border-white/20 transition-all"
                >
                  <span className="w-1.5 h-1.5 bg-violet-400 rounded-full" />
                  <span className="font-mono">{settings.model}</span>
                  <ChevronDown className="w-3.5 h-3.5 text-zinc-400" />
                </button>
                {showModels && (
                  <div
                    onClick={e => e.stopPropagation()}
                    className="absolute bottom-full mb-2 left-0 w-[320px] max-h-[400px] overflow-y-auto custom-scrollbar bg-zinc-900 border border-white/[0.12] rounded-xl shadow-[0_20px_50px_rgba(0,0,0,0.6)] z-30"
                  >
                    {grouped.virtuals.length > 0 && (
                      <div className="px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-violet-300 border-b border-white/[0.06]">
                        Virtual routers
                      </div>
                    )}
                    {grouped.virtuals.map(m => (
                      <ModelOption
                        key={m.id}
                        model={m}
                        selected={m.id === settings.model}
                        onSelect={() => { setSettings(s => ({ ...s, model: m.id })); setShowModels(false) }}
                      />
                    ))}
                    {grouped.providers.map(([owner, ms]) => (
                      <div key={owner}>
                        <div className="px-3 py-2 text-[10px] font-semibold uppercase tracking-wider text-zinc-400 border-b border-white/[0.06] bg-white/[0.02]">
                          {owner}
                        </div>
                        {ms.map(m => (
                          <ModelOption
                            key={m.id}
                            model={m}
                            selected={m.id === settings.model}
                            onSelect={() => { setSettings(s => ({ ...s, model: m.id })); setShowModels(false) }}
                          />
                        ))}
                      </div>
                    ))}
                    {models.length === 0 && (
                      <div className="p-4 text-[12px] text-zinc-500 text-center">
                        No models loaded. Check your API key in Settings.
                      </div>
                    )}
                  </div>
                )}
              </div>
              <span className="text-[11px] text-zinc-500 font-mono">
                {Math.round(modelContext / 1000)}k context
              </span>
              <button
                onClick={e => { e.stopPropagation(); setShowAdvanced(s => !s); setShowModels(false) }}
                className={clsx(
                  'p-1.5 rounded-lg border transition-all',
                  showAdvanced
                    ? 'bg-violet-500/15 border-violet-500/30 text-violet-300'
                    : 'bg-zinc-900 border-white/[0.1] text-zinc-400 hover:text-white hover:border-white/20',
                )}
                title="Advanced model settings"
              >
                <Settings className="w-3.5 h-3.5" />
              </button>
              {showAdvanced && (
                <AdvancedPopover settings={settings} onChange={setSettings} onClose={() => setShowAdvanced(false)} />
              )}
            </div>

            {/* Composer */}
            <div
              className={clsx(
                'relative rounded-2xl bg-zinc-900 border transition-colors',
                dragOver
                  ? 'border-violet-500 bg-violet-500/5 ring-2 ring-violet-500/30'
                  : 'border-white/[0.1] focus-within:border-violet-500/50',
              )}
              onDragOver={e => { e.preventDefault(); setDragOver(true) }}
              onDragLeave={() => setDragOver(false)}
              onDrop={async e => {
                e.preventDefault()
                setDragOver(false)
                if (e.dataTransfer.files?.length) {
                  const atts = await filesToAttachments(e.dataTransfer.files)
                  setAttachments(prev => [...prev, ...atts])
                }
              }}
            >
              {/* Attachment previews */}
              {attachments.length > 0 && (
                <div className="flex flex-wrap gap-1.5 px-3 pt-2.5">
                  {attachments.map(att => (
                    <div
                      key={att.id}
                      className="relative group rounded-lg border border-white/[0.08] bg-zinc-950/60 overflow-hidden shrink-0"
                      style={{ width: '44px', height: '44px' }}
                    >
                      {att.modality === 'image' && (
                        <img src={att.dataUrl} alt={att.name} className="w-full h-full object-cover" />
                      )}
                      {att.modality === 'audio' && (
                        <div className="w-full h-full flex items-center justify-center bg-zinc-900">
                          <span className="text-[8px] uppercase tracking-wider text-violet-300">{att.mime.split('/')[1]}</span>
                        </div>
                      )}
                      {att.modality === 'video' && (
                        <video src={att.dataUrl} className="w-full h-full object-cover" muted />
                      )}
                      <button
                        onClick={() => setAttachments(prev => prev.filter(a => a.id !== att.id))}
                        className="absolute top-0 right-0 w-4 h-4 rounded-bl-md bg-zinc-950/90 border-l border-b border-white/15 flex items-center justify-center text-zinc-300 hover:text-white hover:bg-rose-500/80 transition-all"
                        title="Remove"
                      >
                        <X className="w-2.5 h-2.5" />
                      </button>
                    </div>
                  ))}
                </div>
              )}
              {dragOver && (
                <div className="px-3 pt-3 text-center text-[11px] text-violet-300 font-medium">
                  Drop image, audio, or video to attach
                </div>
              )}
              <div className="flex items-end gap-2">
                {/* Attach button */}
                <input
                  ref={fileInputRef}
                  type="file"
                  accept={[...ACCEPTED.image, ...ACCEPTED.audio, ...ACCEPTED.video].join(',')}
                  multiple
                  className="hidden"
                  onChange={async e => {
                    if (e.target.files?.length) {
                      const atts = await filesToAttachments(e.target.files)
                      setAttachments(prev => [...prev, ...atts])
                    }
                    e.target.value = ''
                  }}
                />
                <button
                  onClick={() => fileInputRef.current?.click()}
                  disabled={streaming}
                  className="ml-2 mb-2 w-9 h-9 rounded-xl bg-zinc-800 hover:bg-zinc-700 border border-white/[0.1] flex items-center justify-center text-zinc-300 hover:text-white transition-all disabled:opacity-40 shrink-0"
                  title="Attach image / audio / video"
                >
                  <Plus className="w-4 h-4" />
                </button>
                {/* Web search toggle */}
                <button
                  onClick={() => setWebSearchEnabled(s => !s)}
                  disabled={streaming}
                  className={clsx(
                    'mb-2 w-9 h-9 rounded-xl border flex items-center justify-center transition-all disabled:opacity-40 shrink-0',
                    webSearchEnabled
                      ? 'bg-emerald-500/15 border-emerald-500/30 text-emerald-300'
                      : 'bg-zinc-800 border-white/[0.1] text-zinc-500 hover:text-zinc-300',
                  )}
                  title={webSearchEnabled ? 'Web search on (browser-side)' : 'Web search off'}
                >
                  {searching
                    ? <Loader2 className="w-4 h-4 animate-spin" />
                    : <Search className="w-4 h-4" />}
                </button>
                <textarea
                  ref={inputRef}
                  value={input}
                  onChange={e => setInput(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault()
                      send()
                    }
                  }}
                  placeholder={
                    settings.keepHistory
                      ? 'Message Potato, or drop image/audio/video…'
                      : 'Message Potato (no retention), or drop image/audio/video…'
                  }
                  rows={1}
                  className="flex-1 bg-transparent px-2 py-3.5 text-[14px] text-zinc-100 placeholder:text-zinc-500 resize-none focus:outline-none custom-scrollbar"
                  style={{ minHeight: '52px', maxHeight: '200px' }}
                  onInput={e => {
                    const t = e.target as HTMLTextAreaElement
                    t.style.height = 'auto'
                    t.style.height = Math.min(t.scrollHeight, 200) + 'px'
                  }}
                  disabled={streaming}
                />
                <div className="flex items-center gap-1.5 mr-2 mb-2">
                  {streaming ? (
                    <button
                      onClick={stop}
                      className="w-9 h-9 rounded-xl bg-zinc-800 hover:bg-zinc-700 border border-white/[0.1] flex items-center justify-center text-zinc-300 transition-all"
                      title="Stop"
                    >
                      <Square className="w-3.5 h-3.5 fill-current" />
                    </button>
                  ) : (
                    <button
                      onClick={send}
                      disabled={!input.trim() && !attachments.length}
                      className="w-9 h-9 rounded-xl bg-gradient-to-r from-violet-600 to-fuchsia-600 flex items-center justify-center text-white shadow-[0_0_16px_rgba(139,92,246,0.4)] hover:brightness-110 active:scale-95 transition-all disabled:opacity-30 disabled:cursor-not-allowed disabled:shadow-none"
                      title="Send"
                    >
                      <ArrowUp className="w-4 h-4" />
                    </button>
                  )}
                </div>
              </div>
            </div>
            <div className="flex items-center justify-between mt-2 text-[10px] text-zinc-600">
              <span>
                Enter to send · Shift+Enter newline · Drop files · {settings.keepHistory ? 'History saved' : 'No retention'}{!getAuthKey() && ' · Public mode'}
              </span>
              <span className="font-mono">
                {input.length > 0 && `~${Math.max(1, Math.round(input.length / 4))} tokens`}
              </span>
            </div>
          </div>
        </div>
      </div>

      {/* Settings modal */}
      {showSettings && (
        <SettingsModal
          settings={settings}
          models={models}
          onClose={() => setShowSettings(false)}
          onChange={setSettings}
        />
      )}
    </div>
  )
}

function ModelOption({
  model, selected, onSelect,
}: {
  model: ModelInfo
  selected: boolean
  onSelect: () => void
}) {
  const [showInfo, setShowInfo] = useState(false)
  const ctx = model.context_length || model.max_model_len || 0
  const ctxStr = ctx >= 1000 ? `${Math.round(ctx / 1000)}k` : ctx > 0 ? `${ctx}` : ''
  const isVirtual = model.id === 'potato/auto' || model.id.startsWith('potato/auto')
    || model.id === 'auto' || model.id.startsWith('openrouter/') || model.id.startsWith('kilo')
  return (
    <div className="relative flex items-center group">
      <button
        onClick={onSelect}
        className={clsx(
          'flex-1 flex items-center justify-between gap-2 px-3 py-2 text-left transition-colors min-w-0',
          selected ? 'bg-violet-500/15 text-violet-200' : 'text-zinc-300 hover:bg-white/[0.04]',
        )}
      >
        <div className="flex items-center gap-2 min-w-0">
          {selected ? <Check className="w-3.5 h-3.5 text-violet-400 shrink-0" /> : <span className="w-3.5" />}
          <span className="font-mono text-[12px] truncate">{model.id}</span>
          {isVirtual && (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-violet-500/20 text-violet-300 border border-violet-500/30 shrink-0">
              auto
            </span>
          )}
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {ctxStr && <span className="text-[10px] text-zinc-500 font-mono">{ctxStr}</span>}
          {model.supports_tools && <Wrench className="w-3 h-3 text-zinc-500" />}
          {model.supports_vision && <Edit3 className="w-3 h-3 text-zinc-500" />}
        </div>
      </button>
      <button
        onClick={e => { e.stopPropagation(); setShowInfo(s => !s) }}
        className="p-1.5 mr-1 rounded-md text-zinc-500 hover:text-violet-300 hover:bg-white/[0.06] transition-all shrink-0"
        title="Model info"
      >
        <Info className="w-3.5 h-3.5" />
      </button>
      {showInfo && (
        <ModelInfoPopover model={model} onClose={() => setShowInfo(false)} />
      )}
    </div>
  )
}

function ModelInfoPopover({ model, onClose }: { model: ModelInfo; onClose: () => void }) {
  const ctx = model.context_length || model.max_model_len
  const maxOut = model.max_output_tokens
  const owner = model.owned_by || (model.id.includes('/') ? model.id.split('/')[0] : 'unknown')
  const isVirtual = model.id === 'potato/auto' || model.id.startsWith('potato/auto')
    || model.id === 'auto' || model.id.startsWith('openrouter/') || model.id.startsWith('kilo')
  const score = scoreCache.get(model.id)
  const tierInfo = getTierInfo(model.id)
  const chainModels = getChainModels(model.id)
  // close on outside click
  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (!(e.target as HTMLElement).closest('.model-info-popover')) onClose()
    }
    window.addEventListener('click', h)
    return () => window.removeEventListener('click', h)
  }, [onClose])

  return (
    <div
      className="model-info-popover absolute bottom-full mb-2 right-0 w-[340px] max-w-[92vw] max-h-[460px] overflow-y-auto custom-scrollbar bg-zinc-900 border border-white/[0.12] rounded-xl shadow-[0_20px_50px_rgba(0,0,0,0.6)] z-40 animate-[fadeIn_0.15s_ease-out]"
      onClick={e => e.stopPropagation()}
    >
      {/* Header */}
      <div className="px-3.5 py-3 border-b border-white/[0.08] flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="text-[13px] font-bold text-white font-mono truncate">{model.id}</div>
          <div className="text-[10px] text-zinc-400 mt-0.5">by {owner}</div>
        </div>
        {isVirtual && (
          <span className="text-[9px] px-1.5 py-0.5 rounded bg-violet-500/20 text-violet-300 border border-violet-500/30 shrink-0 uppercase tracking-wider">
            virtual
          </span>
        )}
      </div>

      <div className="p-3.5 space-y-2.5 text-[11px]">
        {/* Tier description for virtual routers */}
        {tierInfo && (
          <>
            <div className="bg-violet-500/10 border border-violet-500/20 rounded-lg p-2.5">
              <div className="text-[11px] font-bold text-violet-200 mb-1">{tierInfo.title}</div>
              <p className="text-[10.5px] text-zinc-300 leading-relaxed">{tierInfo.desc}</p>
            </div>
            <InfoRow label="Use case" value={tierInfo.useCase} valueClass="text-zinc-300 text-left text-[10.5px] truncate" />
          </>
        )}

        {/* Spec rows */}
        {ctx && (
          <InfoRow label="Context window" value={`${ctx.toLocaleString()} tokens (${Math.round(ctx / 1000)}k)`} />
        )}
        {!ctx && isVirtual && (
          <InfoRow label="Context window" value="dynamic (per pick)" />
        )}
        {maxOut && <InfoRow label="Max output" value={`${maxOut.toLocaleString()} tokens`} />}
        {score?.score != null && <InfoRow label="Quality score" value={`${score.score.toFixed(1)} / 100`} />}
        {score?.intelligence != null && <InfoRow label="Intelligence" value={score.intelligence.toFixed(2)} />}
        {score?.speed != null && <InfoRow label="Speed" value={score.speed.toFixed(2)} />}
        {score?.health != null && (
          <InfoRow
            label="Health"
            value={score.unhealthy ? 'degraded' : 'healthy'}
            valueClass={score.unhealthy ? 'text-amber-400' : 'text-emerald-400'}
          />
        )}

        {/* Capabilities */}
        <div className="flex flex-wrap gap-1.5 pt-1">
          {model.supports_tools && <CapabilityBadge label="Tools" />}
          {model.supports_vision && <CapabilityBadge label="Vision" />}
          {isVirtual && <CapabilityBadge label="Auto-router" />}
          {!isVirtual && !model.supports_tools && !model.supports_vision && (
            <CapabilityBadge label="Text only" muted />
          )}
        </div>

        {/* For virtual routers: show which models it picks from */}
        {isVirtual && chainModels.length > 0 && (
          <div className="pt-2 border-t border-white/[0.06]">
            <div className="text-[10px] text-zinc-500 uppercase tracking-wider font-semibold mb-1.5">
              Picks from ({chainModels.length}{getChainModels(model.id).length > 8 ? '+' : ''} models)
            </div>
            <div className="space-y-1 max-h-[120px] overflow-y-auto custom-scrollbar">
              {chainModels.map(m => {
                const ms = scoreCache.get(m)
                return (
                  <div key={m} className="flex items-center justify-between gap-2 px-2 py-1 rounded-md bg-white/[0.02] border border-white/[0.04]">
                    <span className="font-mono text-[10px] text-zinc-300 truncate">{m}</span>
                    {ms?.score != null && (
                      <span className="text-[10px] font-mono text-violet-300 shrink-0">{ms.score.toFixed(0)}</span>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        )}
        {isVirtual && chainModels.length === 0 && (
          <p className="text-[10.5px] text-zinc-400 leading-relaxed pt-1.5 border-t border-white/[0.06]">
            Virtual router — dynamically picks the best upstream model per request
            based on intent, quality, latency, and provider health.
          </p>
        )}
      </div>
    </div>
  )
}

function InfoRow({ label, value, valueClass }: { label: string; value: string; valueClass?: string }) {
  return (
    <div className="flex justify-between items-center gap-2">
      <span className="text-zinc-500">{label}</span>
      <span className={clsx('font-mono font-semibold text-zinc-200', valueClass)}>{value}</span>
    </div>
  )
}

function CapabilityBadge({ label, muted }: { label: string; muted?: boolean }) {
  return (
    <span className={clsx(
      'px-2 py-0.5 rounded-md text-[10px] font-medium border',
      muted
        ? 'bg-zinc-800/60 text-zinc-400 border-white/[0.06]'
        : 'bg-violet-500/15 text-violet-300 border-violet-500/25',
    )}>
      {label}
    </span>
  )
}

function AdvancedPopover({
  settings, onChange, onClose,
}: {
  settings: ChatSettings
  onChange: (s: ChatSettings) => void
  onClose: () => void
}) {
  useEffect(() => {
    const h = (e: MouseEvent) => {
      if (!(e.target as HTMLElement).closest('.advanced-popover')) onClose()
    }
    window.addEventListener('click', h)
    return () => window.removeEventListener('click', h)
  }, [onClose])

  const isDefault =
    settings.topP === 1.0 &&
    settings.frequencyPenalty === 0 &&
    settings.presencePenalty === 0 &&
    !settings.stop.trim()

  return (
    <div
      className="advanced-popover absolute bottom-full mb-2 right-0 w-[300px] max-w-[92vw] bg-zinc-900 border border-white/[0.12] rounded-xl shadow-[0_20px_50px_rgba(0,0,0,0.6)] z-40 animate-[fadeIn_0.15s_ease-out]"
      onClick={e => e.stopPropagation()}
    >
      <div className="px-3.5 py-3 border-b border-white/[0.08] flex items-center justify-between">
        <div className="text-[12px] font-semibold text-white">Advanced settings</div>
        {!isDefault && (
          <button
            onClick={() => onChange({
              ...settings,
              topP: 1.0, frequencyPenalty: 0, presencePenalty: 0, stop: '',
            })}
            className="text-[10px] text-zinc-400 hover:text-violet-300"
          >
            Reset
          </button>
        )}
      </div>
      <div className="p-3.5 space-y-3.5 text-[11px] max-h-[340px] overflow-y-auto custom-scrollbar">
        {/* Temperature */}
        <div>
          <div className="flex justify-between items-center mb-1">
            <label className="text-zinc-300 font-medium">Temperature</label>
            <span className="font-mono text-violet-300 font-bold">{settings.temperature}</span>
          </div>
          <input
            type="range" min={0} max={2} step={0.05}
            value={settings.temperature}
            onChange={e => onChange({ ...settings, temperature: parseFloat(e.target.value) })}
            className="w-full accent-violet-500 cursor-pointer"
          />
          <p className="text-[10px] text-zinc-500 mt-0.5">Lower = focused. Higher = creative.</p>
        </div>

        {/* Max tokens */}
        <div>
          <div className="flex justify-between items-center mb-1">
            <label className="text-zinc-300 font-medium">Max output tokens</label>
            <span className="font-mono text-violet-300 font-bold">{settings.maxTokens}</span>
          </div>
          <input
            type="range" min={256} max={64000} step={256}
            value={settings.maxTokens}
            onChange={e => onChange({ ...settings, maxTokens: parseInt(e.target.value) })}
            className="w-full accent-violet-500 cursor-pointer"
          />
        </div>

        {/* Top P */}
        <div>
          <div className="flex justify-between items-center mb-1">
            <label className="text-zinc-300 font-medium">Top P (nucleus)</label>
            <span className="font-mono text-violet-300 font-bold">{settings.topP}</span>
          </div>
          <input
            type="range" min={0} max={1} step={0.05}
            value={settings.topP}
            onChange={e => onChange({ ...settings, topP: parseFloat(e.target.value) })}
            className="w-full accent-violet-500 cursor-pointer"
          />
        </div>

        {/* Frequency penalty */}
        <div>
          <div className="flex justify-between items-center mb-1">
            <label className="text-zinc-300 font-medium">Frequency penalty</label>
            <span className="font-mono text-violet-300 font-bold">{settings.frequencyPenalty}</span>
          </div>
          <input
            type="range" min={-2} max={2} step={0.1}
            value={settings.frequencyPenalty}
            onChange={e => onChange({ ...settings, frequencyPenalty: parseFloat(e.target.value) })}
            className="w-full accent-violet-500 cursor-pointer"
          />
          <p className="text-[10px] text-zinc-500 mt-0.5">Reduces repetition of tokens.</p>
        </div>

        {/* Presence penalty */}
        <div>
          <div className="flex justify-between items-center mb-1">
            <label className="text-zinc-300 font-medium">Presence penalty</label>
            <span className="font-mono text-violet-300 font-bold">{settings.presencePenalty}</span>
          </div>
          <input
            type="range" min={-2} max={2} step={0.1}
            value={settings.presencePenalty}
            onChange={e => onChange({ ...settings, presencePenalty: parseFloat(e.target.value) })}
            className="w-full accent-violet-500 cursor-pointer"
          />
          <p className="text-[10px] text-zinc-500 mt-0.5">Encourages new topics.</p>
        </div>

        {/* Stop sequences */}
        <div>
          <label className="block text-zinc-300 font-medium mb-1">Stop sequences</label>
          <input
            type="text"
            value={settings.stop}
            onChange={e => onChange({ ...settings, stop: e.target.value })}
            placeholder="e.g. \n, END, ###"
            className="w-full bg-zinc-950/80 border border-white/[0.1] text-zinc-100 px-2.5 py-1.5 rounded-lg text-[11px] focus:outline-none focus:border-violet-500 font-mono"
          />
          <p className="text-[10px] text-zinc-500 mt-0.5">Comma-separated. Stops generation at any match.</p>
        </div>

        {/* Stream toggle */}
        <div className="flex items-center justify-between p-2 rounded-lg bg-zinc-950/60 border border-white/[0.06]">
          <div>
            <p className="text-[11px] font-medium text-zinc-200">Stream response</p>
            <p className="text-[10px] text-zinc-500">Token-by-token (SSE) vs full response</p>
          </div>
          <button
            onClick={() => onChange({ ...settings, stream: !settings.stream })}
            className={clsx(
              'relative w-10 h-6 rounded-full transition-colors',
              settings.stream ? 'bg-violet-500' : 'bg-zinc-700',
            )}
          >
            <span className={clsx(
              'absolute top-0.5 w-5 h-5 rounded-full bg-white transition-transform',
              settings.stream ? 'translate-x-[18px]' : 'translate-x-0.5',
            )} />
          </button>
        </div>
      </div>
    </div>
  )
}

function SettingsModal({
  settings, models, onClose, onChange,
}: {
  settings: ChatSettings
  models: ModelInfo[]
  onClose: () => void
  onChange: (s: ChatSettings) => void
}) {
  const [keyInput, setKeyInput] = useState(() => getAuthKey())
  const model = models.find(m => m.id === settings.model)
  const ctx = model?.context_length || model?.max_model_len || DEFAULT_CONTEXT

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/70 backdrop-blur-md" onClick={onClose} />
      <div className="relative w-full max-w-md bg-zinc-900 border border-white/[0.12] rounded-2xl shadow-[0_32px_80px_rgba(0,0,0,0.8)] z-10 animate-[fadeIn_0.2s_ease-out]">
        <div className="px-6 py-4 border-b border-white/[0.08] flex items-center justify-between">
          <h3 className="text-base font-semibold text-white">Chat settings</h3>
          <button onClick={onClose} className="p-1.5 rounded-lg text-zinc-400 hover:text-white hover:bg-white/[0.06]">
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="p-6 space-y-5 max-h-[80vh] overflow-y-auto custom-scrollbar">
          <div>
            <label className="block text-[11px] font-semibold text-zinc-300 uppercase tracking-wider mb-1.5">API key</label>
            <input
              type="password"
              value={keyInput}
              onChange={e => setKeyInput(e.target.value)}
              placeholder="sk-nk-… or PROXY_API_KEYS"
              className="w-full bg-zinc-950/80 border border-white/[0.1] text-zinc-100 px-3.5 py-2.5 rounded-xl text-[13px] focus:outline-none focus:border-violet-500 font-mono"
            />
            <p className="text-[11px] text-zinc-500 mt-1.5">
              Your key is stored only in this browser's localStorage. Clears on logout.
            </p>
            <button
              onClick={() => { setAuthKey(keyInput.trim()); onClose(); window.location.reload() }}
              className="mt-2 px-3 py-1.5 rounded-lg bg-violet-500/15 border border-violet-500/25 text-violet-300 text-[12px] font-medium hover:bg-violet-500/20"
            >
              Save key
            </button>
          </div>

          <div>
            <label className="block text-[11px] font-semibold text-zinc-300 uppercase tracking-wider mb-1.5">Model</label>
            <select
              value={settings.model}
              onChange={e => onChange({ ...settings, model: e.target.value })}
              className="w-full bg-zinc-950/80 border border-white/[0.1] text-zinc-100 px-3.5 py-2.5 rounded-xl text-[13px] focus:outline-none focus:border-violet-500 font-mono cursor-pointer"
            >
              {models.length === 0 && <option value={settings.model}>{settings.model} (no models loaded)</option>}
              {models.filter(m => {
                const isVirtual = m.id === 'potato/auto' || m.id.startsWith('potato/auto') || m.id === 'auto'
                  || m.id.startsWith('openrouter/') || m.id.startsWith('kilo')
                return isVirtual || settings.showAllModels
              }).map(m => <option key={m.id} value={m.id}>{m.id}</option>)}
            </select>
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              <span className="text-[11px] text-zinc-500">
                Context: <span className="font-mono text-zinc-300 font-semibold">{Math.round(ctx / 1000)}k</span>
              </span>
              {model?.supports_tools && <CapabilityBadge label="Tools" />}
              {model?.supports_vision && <CapabilityBadge label="Vision" />}
              {model?.max_output_tokens && (
                <span className="text-[11px] text-zinc-500">
                  Out: <span className="font-mono text-zinc-300 font-semibold">
                    {Math.round(model.max_output_tokens / 1000)}k
                  </span>
                </span>
              )}
            </div>
          </div>

          {/* Show all models toggle */}
          <div className="flex items-center justify-between p-3 rounded-xl bg-zinc-950/60 border border-white/[0.08]">
            <div>
              <p className="text-[13px] font-medium text-zinc-200">Show all models in picker</p>
              <p className="text-[11px] text-zinc-500">
                {settings.showAllModels
                  ? 'Every upstream model is listed.'
                  : 'Only potato/* router models are shown.'}
              </p>
            </div>
            <button
              onClick={() => onChange({ ...settings, showAllModels: !settings.showAllModels })}
              className={clsx(
                'relative w-10 h-6 rounded-full transition-colors',
                settings.showAllModels ? 'bg-violet-500' : 'bg-zinc-700',
              )}
            >
              <span className={clsx(
                'absolute top-0.5 w-5 h-5 rounded-full bg-white transition-transform',
                settings.showAllModels ? 'translate-x-[18px]' : 'translate-x-0.5',
              )} />
            </button>
          </div>

          <div>
            <div className="flex justify-between items-center mb-1.5">
              <label className="text-[11px] font-semibold text-zinc-300 uppercase tracking-wider">Temperature</label>
              <span className="font-mono text-[12px] text-violet-300 font-bold">{settings.temperature}</span>
            </div>
            <input
              type="range" min={0} max={2} step={0.1}
              value={settings.temperature}
              onChange={e => onChange({ ...settings, temperature: parseFloat(e.target.value) })}
              className="w-full accent-violet-500 cursor-pointer"
            />
          </div>

          <div>
            <label className="block text-[11px] font-semibold text-zinc-300 uppercase tracking-wider mb-1.5">Max tokens</label>
            <input
              type="number" min={1} max={64000}
              value={settings.maxTokens}
              onChange={e => onChange({ ...settings, maxTokens: parseInt(e.target.value) || 4096 })}
              className="w-full bg-zinc-950/80 border border-white/[0.1] text-zinc-100 px-3.5 py-2.5 rounded-xl text-[13px] focus:outline-none focus:border-violet-500 font-mono"
            />
          </div>

          <div>
            <label className="block text-[11px] font-semibold text-zinc-300 uppercase tracking-wider mb-1.5">
              System prompt (optional)
            </label>
            <textarea
              value={settings.systemPrompt}
              onChange={e => onChange({ ...settings, systemPrompt: e.target.value })}
              rows={3}
              placeholder="e.g. Be concise. Answer in the user's language."
              className="w-full bg-zinc-950/80 border border-white/[0.1] text-zinc-100 px-3.5 py-2.5 rounded-xl text-[13px] focus:outline-none focus:border-violet-500 resize-y"
            />
          </div>

          <div className="flex items-center justify-between p-3 rounded-xl bg-zinc-950/60 border border-white/[0.08]">
            <div className="flex items-center gap-2">
              {settings.keepHistory
                ? <Shield className="w-4 h-4 text-emerald-400" />
                : <ShieldOff className="w-4 h-4 text-amber-400" />
              }
              <div>
                <p className="text-[13px] font-medium text-zinc-200">
                  {settings.keepHistory ? 'Keep chat history' : 'No retention'}
                </p>
                <p className="text-[11px] text-zinc-500">
                  {settings.keepHistory ? 'Saved in this browser only' : 'History cleared on tab close'}
                </p>
              </div>
            </div>
            <button
              onClick={() => onChange({ ...settings, keepHistory: !settings.keepHistory })}
              className={clsx(
                'relative w-10 h-6 rounded-full transition-colors',
                settings.keepHistory ? 'bg-emerald-500' : 'bg-zinc-700',
              )}
            >
              <span className={clsx(
                'absolute top-0.5 w-5 h-5 rounded-full bg-white transition-transform',
                settings.keepHistory ? 'translate-x-[18px]' : 'translate-x-0.5',
              )} />
            </button>
          </div>
        </div>
        <div className="px-6 py-4 border-t border-white/[0.08] flex justify-end">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-xl bg-violet-600 text-white text-[13px] font-medium hover:bg-violet-500"
          >
            Done
          </button>
        </div>
      </div>
    </div>
  )
}