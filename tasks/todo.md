# Tasks: Self-Healing Cloudflare Tunnel Integration & Plug-and-Play Output

- [x] Add `cloudflared` auto-installer logic in `deploy.sh` <!-- id: 0 -->
- [x] Create self-healing `cloudflared-potato.service` systemd daemon in `deploy.sh` <!-- id: 1 -->
- [x] Implement URL discovery to auto-detect `trycloudflare.com` HTTPS link and update `.env` <!-- id: 2 -->
- [x] Update `deploy.sh` summary banner into a single unified plug-and-play output <!-- id: 3 -->
- [x] Document `ENABLE_CLOUDFLARE_TUNNEL` and `CLOUDFLARE_TUNNEL_TOKEN` in `.env.example` <!-- id: 4 -->
- [x] Verify script syntax with `bash -n deploy.sh` <!-- id: 5 -->

## Review & Results
- **Cloudflare Tunnel Auto-Installer**: Auto-detects architecture (`amd64` / `arm64`) and package manager, installing `cloudflared` on Debian/Ubuntu/Fedora/macOS.
- **Self-Healing Daemon**: Provisions systemd unit `cloudflared-potato.service` with `--protocol http2` (bypassing UDP/QUIC firewall blocks) and `Restart=always` + `RestartSec=5s` auto-recovery.
- **Dynamic URL Extraction**: Queries metrics server on `127.0.0.1:45678/quicktunnel` and journalctl logs to resolve the active `https://xxx.trycloudflare.com` URL, automatically setting `PUBLIC_BASE_URL` and `SESSION_SECURE_COOKIE=true` in `.env`.
- **Unified Console Banner**: Displays all endpoint URLs, admin credentials (`admin@localhost`), API keys, and integration snippets in a single consolidated screen.
- **Verification**: Verified via `bash -n deploy.sh` and full `pytest` suite (429/429 tests passing).
