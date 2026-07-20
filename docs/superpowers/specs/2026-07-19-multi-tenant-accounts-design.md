# Multi-tenant accounts — Design

**Date:** 2026-07-19  
**Status:** Approved (option 1 + email stub + admin approval)

## Goal

Signup → verify email → **admin approves** → API key issued → user sees **only their** analytics. Admin view is separate (users + full gateway).

## Architecture

- SQLite tables in existing `NimmakaiDB` (no new service)
- Dashboard auth: HTTP-only session cookie (`nk_session`)
- Proxy auth: Bearer API key (`sk-nk-…`) → `user_id` on traces
- Email: stub (`EMAIL_BACKEND=stub`) logs/returns verify links; Resend later
- Admin: `role=admin` (seeded via `ADMIN_EMAILS`) + legacy `PROXY_API_KEYS` as break-glass

## User statuses

`unverified` → `pending_approval` → `active` | `rejected` | `suspended`

API key created **only on approve**.

## Trace attribution

Add `user_id` column to `traces` / rollups. Analytics for non-admins forced to `user_id = me`.
