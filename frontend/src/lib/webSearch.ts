// Web search — calls the server-side proxy at /chat/api/search which queries
// DuckDuckGo HTML (full web, no API key, no CORS limits) + Wikipedia.
// The server proxy bypasses browser CORS so we get real web results (news,
// blogs, docs) instead of just encyclopedic/factoid entries.

export interface SearchResult {
  title: string
  snippet: string
  url: string
  source: 'duckduckgo' | 'wikipedia'
}

export interface SearchResponse {
  query: string
  results: SearchResult[]
  synthesizedContext: string
}

export async function webSearch(query: string): Promise<SearchResponse> {
  try {
    const res = await fetch(`/chat/api/search?q=${encodeURIComponent(query)}`)
    if (!res.ok) {
      return { query, results: [], synthesizedContext: '' }
    }
    const body = await res.json()
    const results: SearchResult[] = (body.results || []).map((r: SearchResult) => ({
      title: r.title,
      snippet: r.snippet,
      url: r.url,
      source: r.source,
    }))
    return {
      query,
      results,
      synthesizedContext: buildContext(query, results),
    }
  } catch {
    return { query, results: [], synthesizedContext: '' }
  }
}

function buildContext(query: string, results: SearchResult[]): string {
  if (!results.length) return ''
  const parts = results.map((r, i) =>
    `[${i + 1}] ${r.title}\n${r.snippet}\nSource: ${r.url}`,
  )
  return `Web search results for "${query}":\n\n${parts.join('\n\n')}\n\nYou have been given web search results above. Use ONLY these results to answer the user's question. Cite sources as [1], [2], etc. Do NOT attempt to call any tools or functions (e.g. search_web) — the search has already been performed for you. If the results do not answer the question, say so plainly.`
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