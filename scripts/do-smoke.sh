#!/usr/bin/env bash
# Smoke-check DigitalOcean deploy artifacts locally.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Checking required files"
test -f Dockerfile
test -f .do/app.yaml
test -f docker-compose.do.yml
test -f docs/digitalocean.md
test -f frontend/package-lock.json
test -f .github/workflows/deploy-digitalocean.yml
test -x scripts/generate-do-userdata.sh

echo "==> Generating sample userdata (non-interactive)"
tmp_ud="$(mktemp)"
NONINTERACTIVE=1 PROXY_API_KEYS=sk-smoke-test \
  ./scripts/generate-do-userdata.sh -o "$tmp_ud"
grep -q 'docker compose -f docker-compose.do.yml up -d --build' "$tmp_ud"
grep -q 'base64 -d' "$tmp_ud"
rm -f "$tmp_ud"

echo "==> Validating app.yaml has deploy_on_push"
grep -q 'deploy_on_push: true' .do/app.yaml

echo "==> Dockerfile build (may take a few minutes)"
docker build -t nimmakai:smoke .

echo "==> Starting container"
cid=$(docker run -d --rm -p 18080:8080 \
  -e PROXY_API_KEYS=sk-smoke-test \
  -e ALLOW_INSECURE_AUTH=false \
  -e NIM_API_KEYS= \
  -e SQLITE_SEED_FREE_PRESETS=true \
  nimmakai:smoke)

cleanup() { docker stop "$cid" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "==> Waiting for /health"
ok=0
for i in $(seq 1 40); do
  if curl -fsS http://127.0.0.1:18080/health >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 1
done
if [ "$ok" != "1" ]; then
  echo "health check failed; logs:"
  docker logs "$cid" || true
  exit 1
fi

curl -fsS http://127.0.0.1:18080/health | head -c 400
echo
echo "==> OK — DigitalOcean Docker image is healthy"
