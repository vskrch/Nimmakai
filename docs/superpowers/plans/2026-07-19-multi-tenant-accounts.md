# Multi-tenant accounts Implementation Plan

> **For agentic workers:** implement task-by-task.

**Goal:** Multi-tenant signup/verify/admin-approve/API-key + scoped analytics vs admin UI.

**Architecture:** SQLite users/keys/sessions; session cookie for dashboard; Bearer key for proxy; `user_id` on traces.

**Tech Stack:** FastAPI, SQLite, stdlib scrypt, React dashboard, email stub.

## Global Constraints

- No new paid deps (email stub; Resend later)
- Key issued only after admin approve
- Non-admin analytics scoped to `user_id`
- Legacy `PROXY_API_KEYS` remain admin break-glass

---

### Task 1: Schema + accounts store
### Task 2: Auth routes + email stub
### Task 3: Admin user approve + authz helpers
### Task 4: Trace user_id + analytics scoping
### Task 5: Frontend auth + user/admin split
### Task 6: Tests
