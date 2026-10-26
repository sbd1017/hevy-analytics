#!/bin/bash
# Local preview at http://127.0.0.1:<port>/hevy/ (port: $PORT, else the first argument, else 8778) (development session for this machine; no Hevy key used).
# Data: HEVY_DATA_DIR (default var-dev/). Load data with `python -m hevy.cli import-dump FILE` (development only).
set -euo pipefail
cd "$(dirname "$0")/.."
P=${PORT:-${1:-8778}}
export HEVY_ENV=development HEVY_DATA_DIR="${HEVY_DATA_DIR:-var-dev}" HEVY_PUBLIC_ORIGINS="http://127.0.0.1:$P,http://localhost:$P"
unset SITE_AUTH
exec .venv/bin/python -c "import uvicorn; from hevy.web import create_app; uvicorn.run(create_app(), host='127.0.0.1', port=$P, access_log=False, server_header=False)"
