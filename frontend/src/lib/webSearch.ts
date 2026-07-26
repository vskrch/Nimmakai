// Browser-side web search — runs in the user's browser, uses their IP, no server key.
// Sources: Wikipedia (CORS-enabled), DuckDuckGo Instant Answers (CORS-enabled).
// Results are fed as context to the LLM; the LLM synthesizes the answer.

export interface SearchResult {
  title: string
  snippet: string
  url: string
  source: 'wikipedia' | 'duckduckgo'
}

export interface SearchResponse {
  query: string
  results: SearchResult[]
  synthesizedContext: string
}

const MAX_RESULTS = 5
const SNIPPET_CHARS = 400

async function searchWikipedia(query: string): Promise<SearchResult[]> {
  try {
    const url = `https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=${encodeURIComponent(query)}&format=json&origin=*&srlimit=${MAX_RESULTS}`
    const res = await fetch(url)
    if (!res.ok) return []
    const body = await res.json()
    const items = body?.query?.search || []
    return items.map((item: { title: string; snippet: string; pageid: number }) => ({
      title: item.title,
      snippet: stripHtml(item.snippet).slice(0, SNIPPET_CHARS),
      url: `https://en.wikipedia.org/?curid=${item.pageid}`,
      source: 'wikipedia' as const,
    }))
  } catch {
    return []
  }
}

async function searchDuckDuckGo(query: string): Promise<SearchResult[]> {
  try {
    // DDG Instant Answer API — CORS-enabled, returns related topics
    const url = `https://api.duckduckgo.com/?q=${encodeURIComponent(query)}&format=json&no_html=1&skip_disambig=1`
    const res = await fetch(url)
    if (!res.ok) return []
    const body = await res.json()
    const results: SearchResult[] = []
    // Abstract (top answer)
    if (body.Abstract) {
      results.push({
        title: body.Heading || query,
        snippet: body.Abstract.slice(0, SNIPPET_CHARS),
        url: body.AbstractURL || `https://duckduckgo.com/?q=${encodeURIComponent(query)}`,
        source: 'duckduckgo',
      })
    }
    // Related topics
    const topics: Array<{ Text: string; FirstURL: string }> = body.RelatedTopics || []
    for (const t of topics) {
      if (t.Text && t.FirstURL) {
        results.push({
          title: t.Text.split(' - ')[0] || query,
          snippet: t.Text.slice(0, SNIPPET_CHARS),
          url: t.FirstURL,
          source: 'duckduckgo',
        })
      }
      if (results.length >= MAX_RESULTS) break
    }
    return results.slice(0, MAX_RESULTS)
  } catch {
    return []
  }
}

function stripHtml(html: string): string {
  return html.replace(/<[^>]*>/g, '').replace(/&[a-z]+;/g, ' ').replace(/\s+/g, ' ').trim()
}

export async function webSearch(query: string): Promise<SearchResponse> {
  const [wiki, ddg] = await Promise.allSettled([searchWikipedia(query), searchDuckDuckGo(query)])
  const wikiResults = wiki.status === 'fulfilled' ? wiki.value : []
  const ddgResults = ddg.status === 'fulfilled' ? ddg.value : []
  // De-dup by title, prefer Wikipedia first
  const seen = new Set<string>()
  const results: SearchResult[] = []
  for (const r of [...wikiResults, ...ddgResults]) {
    const key = r.title.toLowerCase().slice(0, 60)
    if (seen.has(key)) continue
    seen.add(key)
    results.push(r)
    if (results.length >= MAX_RESULTS) break
  }
  const synthesizedContext = buildContext(query, results)
  return { query, results, synthesizedContext }
}

function buildContext(query: string, results: SearchResult[]): string {
  if (!results.length) return ''
  const parts = results.map((r, i) =>
    `[${i + 1}] ${r.title}\n${r.snippet}\nSource: ${r.url}`,
  )
  return `Web search results for "${query}":\n\n${parts.join('\n\n')}\n\nSynthesize a grounded answer using these sources. Cite as [1], [2], etc. If the sources don't answer the question, say so.`
}

// Heuristic: does this message look like it needs fresh info from the web?
export function looksLikeSearchQuery(text: string): boolean {
  const lower = text.toLowerCase()
  // Explicit search verbs / current-events triggers
  const triggers = [
    'search for', 'look up', 'what is the latest', 'what\'s new',
    'current', 'today', 'recent', 'now', 'latest', 'news',
    'price of', 'stock', 'weather', 'score', 'who is', 'who won',
    'when is', 'where is', 'how do i',
  ]
  // Long-tail questions ("who is X", "what happened in 2024")
  if (/\b(who|what|when|where|why|how)\b/.test(lower) && lower.includes('?')) return true
  for (const t of triggers) {
    if (lower.includes(t)) return true
  }
  return false
}