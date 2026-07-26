# Tasks: Seamless First-Time Admin Onboarding & Production Polish (P0-1, P1-3, UX-1)

- [x] Add `admin_email` and `admin_password` to `Settings` in `src/potato/config.py` <!-- id: 0 -->
- [x] Implement automatic admin seeding in `_init_accounts` inside `src/potato/main.py` <!-- id: 1 -->
- [x] Update `deploy.sh` to write `ADMIN_EMAIL` to `.env` and display clean login instructions in summary <!-- id: 2 -->
- [x] Update `.env.example` with `ADMIN_EMAIL` and `ADMIN_PASSWORD` documentation <!-- id: 3 -->
- [x] Add unit test verifying auto-seeding of admin account from settings <!-- id: 4 -->
- [x] Implement P1-3: Sanitize `/health` endpoint to strip sensitive provider topology for anonymous callers <!-- id: 5 -->
- [x] Implement UX-1: Add wildcard `{path:path}` catch-all decorators in `main.py` for React SPA deep links (`/dashboard` and `/chat`) <!-- id: 6 -->
- [x] Run test suite (`pytest` and `bash -n deploy.sh`) <!-- id: 7 -->

## Review & Results
- **Admin Onboarding (P0-1)**: Idempotently seeds `admin@localhost` (or configured email) with `ADMIN_PASSWORD` as a verified, active administrator on startup.
- **Topology Security (P1-3)**: Anonymous network requests to `GET /health` only receive summary status (`status`, `version`, `active_providers`, `live_models`, `catalog_ok`, `proxy_auth_configured`). Granular numeric key counts and provider topologies (`providers`) are only disclosed when authenticated via `resolve_auth()`.
- **SPA Deep-Link UX (UX-1)**: Added `@app.get("/dashboard/{path:path}")` and `@app.get("/chat/{path:path}")` catch-all decorators in `main.py` so browser refreshes on client-side routes (e.g. `/dashboard/models` or `/chat/settings`) serve the compiled React SPA bundle instead of returning 404 Not Found.
- **Verification**: Verified via `pytest` (all 429 unit and integration tests passing in 11.4s) and `bash -n deploy.sh`.
