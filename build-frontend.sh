#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/frontend"
npm install --production=false
npx tsc --noEmit
npx vite build
echo "Frontend built → src/nimmakai/static/dist/"
