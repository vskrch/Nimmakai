# Tasks: Fix Dashboard Runtime Error & PWA 404 Files

- [x] Fix `TypeError: Cannot read properties of undefined (reading 'toLocaleString')` in `frontend/src/components/charts.tsx`, `AnalyticsOverviewPage.tsx`, `DashboardPage.tsx`, `IntentsPage.tsx`, `RLPage.tsx`, `CostPage.tsx`, `ChatPage.tsx` by using `fmtNum()` safely <!-- id: 0 -->
- [x] Mount root PWA static files (`/manifest.json`, `/sw.js`, icons) from `src/potato/static/dist/` in `src/potato/main.py` to fix 404 errors <!-- id: 1 -->
- [x] Rebuild frontend bundle with `./build-frontend.sh` <!-- id: 2 -->
- [x] Verify test suite with `pytest` and linter with `ruff check .` <!-- id: 3 -->

## Review & Results
- **Crash Fix**: Wrapped numeric values in `charts.tsx`, `IntentsPage.tsx`, `RLPage.tsx`, and `AnalyticsOverviewPage.tsx` with safe nullish fallbacks `(val ?? 0).toLocaleString()` and `fmtNum(x)` so undefined or uninitialized metrics never trigger a `TypeError`.
- **PWA Routes**: Added FastAPI handlers in `src/potato/main.py` for `/manifest.json`, `/sw.js`, `/icon-*.png`, and `/maskable-*.png` so PWA manifest and Service Worker registrations resolve with HTTP 200 OK.
- **Verification**: Verified via `ruff check .` (0 errors), `./build-frontend.sh` (built successfully), and full `pytest` suite (452/452 tests passing).
