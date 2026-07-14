# Lessons Learned

## Model Catalogs and Benchmark Rankings
- **Context:** When updating routing tables, model configurations, or capability/quality ladders, always reference the most up-to-date benchmarks and SOTA models from `models.dev`, `arena.ai` (LMSYS), `swebenchpro`, and `artificialanalysis.ai`.
- **Lesson:** Do not rely on outdated default model lists or older baseline configurations. Keep catalog records fresh, and adapt immediately when the user highlights newer models (such as OpenCode MiMo, DeepSeek V4 Pro, Kimi K2.6, and Grok 4.5 in 2026).
- **Rule:** Prioritize the absolute highest-ranking models on SWE-bench Pro and Terminal Bench for coding tasks.

## Production multi-provider routing
- **Context:** Features appeared dead in production (empty auto routes, dashboard failures, silent 404 cascades).
- **Lesson:** Never silently cross-route a namespaced model to the wrong provider; filter chains by active provider runtimes; resolve config paths beyond CWD; expose routing headers over CORS; dashboard must surface auth/catalog/setup errors instead of failing silently; empty preference chain should clear pins.
- **Rule:** Production health must distinguish “process up” from “keys + live catalog ready”.
