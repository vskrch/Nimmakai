import React from 'react'

// ponytail: minimal markdown — fences, inline code, bold/italic, links, lists, headings.
// No deps. Good enough for chat output. Upgrade to marked/react-markdown if needed.

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!
  ))
}

function renderInline(text: string, keyBase: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = []
  // Order: inline code, bold, italic, links
  const regex = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*]+\*)|(\[[^\]]+\]\([^)]+\))/g
  let last = 0
  let m: RegExpExecArray | null
  let i = 0
  while ((m = regex.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index))
    const tok = m[0]
    if (tok.startsWith('`')) {
      nodes.push(
        <code key={`${keyBase}-c-${i}`} className="px-1.5 py-0.5 rounded-md bg-zinc-800/80 text-violet-300 font-mono text-[12px] border border-white/[0.06]">
          {tok.slice(1, -1)}
        </code>
      )
    } else if (tok.startsWith('**')) {
      nodes.push(<strong key={`${keyBase}-b-${i}`} className="font-semibold text-white">{tok.slice(2, -2)}</strong>)
    } else if (tok.startsWith('*')) {
      nodes.push(<em key={`${keyBase}-i-${i}`} className="italic">{tok.slice(1, -1)}</em>)
    } else if (tok.startsWith('[')) {
      const match = /\[([^\]]+)\]\(([^)]+)\)/.exec(tok)
      if (match) {
        nodes.push(
          <a key={`${keyBase}-l-${i}`} href={match[2]} target="_blank" rel="noreferrer" className="text-violet-400 hover:text-violet-300 underline underline-offset-2">
            {match[1]}
          </a>
        )
      }
    }
    last = m.index + tok.length
    i++
  }
  if (last < text.length) nodes.push(text.slice(last))
  return nodes
}

export function Markdown({ content }: { content: string }) {
  const blocks = content.split(/```/)
  const out: React.ReactNode[] = []
  blocks.forEach((block, bi) => {
    if (bi % 2 === 1) {
      // fenced code block
      const nl = block.indexOf('\n')
      const lang = nl > 0 ? block.slice(0, nl).trim() : ''
      const code = nl > 0 ? block.slice(nl + 1) : block
      out.push(
        <pre key={`pre-${bi}`} className="my-3 p-4 rounded-xl bg-zinc-950 border border-white/[0.08] overflow-x-auto custom-scrollbar">
          {lang && <div className="text-[10px] text-zinc-500 uppercase tracking-widest mb-2 font-semibold select-none">{lang}</div>}
          <code className="font-mono text-[12.5px] text-zinc-200 leading-relaxed whitespace-pre">{code.replace(/\n$/, '')}</code>
        </pre>
      )
      return
    }
    const lines = block.split('\n')
    let i = 0
    let para: string[] = []
    const flushPara = () => {
      if (para.length) {
        out.push(<p key={`p-${bi}-${i}`} className="my-2 leading-relaxed">{renderInline(para.join(' '), `p-${bi}-${i}`)}</p>)
        para = []
      }
    }
    while (i < lines.length) {
      const line = lines[i]
      const trimmed = line.trim()
      if (!trimmed) { flushPara(); i++; continue }
      // headings
      const h = /^(#{1,4})\s+(.*)$/.exec(trimmed)
      if (h) {
        flushPara()
        const level = h[1].length
        const sizes = ['text-xl', 'text-lg', 'text-base', 'text-sm']
        out.push(
          <p key={`h-${bi}-${i}`} className={`${sizes[level - 1]} font-bold text-white mt-4 mb-2`}>
            {renderInline(h[2], `h-${bi}-${i}`)}
          </p>
        )
        i++; continue
      }
      // bullet list
      if (/^[-*]\s+/.test(trimmed)) {
        flushPara()
        const items: string[] = []
        while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
          items.push(lines[i].replace(/^\s*[-*]\s+/, ''))
          i++
        }
        out.push(
          <ul key={`ul-${bi}-${i}`} className="my-2 ml-5 space-y-1 list-disc list-outside marker:text-zinc-500">
            {items.map((it, j) => <li key={j} className="leading-relaxed">{renderInline(it, `li-${bi}-${j}`)}</li>)}
          </ul>
        )
        continue
      }
      // numbered list
      if (/^\d+\.\s+/.test(trimmed)) {
        flushPara()
        const items: string[] = []
        while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
          items.push(lines[i].replace(/^\s*\d+\.\s+/, ''))
          i++
        }
        out.push(
          <ol key={`ol-${bi}-${i}`} className="my-2 ml-5 space-y-1 list-decimal list-outside marker:text-zinc-500">
            {items.map((it, j) => <li key={j} className="leading-relaxed">{renderInline(it, `li2-${bi}-${j}`)}</li>)}
          </ol>
        )
        continue
      }
      para.push(trimmed)
      i++
    }
    flushPara()
  })
  return <div className="text-[14px] text-zinc-100">{out}</div>
}