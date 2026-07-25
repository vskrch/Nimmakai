# Task Plan: Agent Tool Integrations (Cursor, OpenCode, Cline, Claude Code) & Production Ready README

- [x] 1. Expand `_AUTO_ALIAS` in `auto_router.py` for agent model names (`cursor/auto`, `claude-3-5-sonnet`, `opencode-go`, `cline/auto`, etc.)
- [x] 2. Enrich `/v1/models` responses with context window & capability metadata in `registry.py`
- [x] 3. Update `claude.py` to support `x-api-key` header for Claude Code CLI and Anthropic SDK compatibility
- [x] 4. Update `openai.py` for `stream_options` and Cursor checksum headers
- [x] 5. Revamp `README.md` with integration guides for Cursor, Claude Code CLI, Cline, OpenCode, and DigitalOcean
- [x] 6. Run `pytest tests/` and `./build-frontend.sh` to verify zero errors (337 passed, 0 failed)
