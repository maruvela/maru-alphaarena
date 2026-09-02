#!/usr/bin/env bash
set -euo pipefail

# Alpha Arena 로컬 실행 스크립트.
# .env는 AWS_REGION / BEDROCK_MODEL_ID 등 REQUIREMENTS.md 21장의 값을 채워야 한다.

cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    python -m venv .venv
fi

source .venv/Scripts/activate 2>/dev/null || source .venv/bin/activate

pip install -q -r requirements.txt

exec uvicorn src.app:app --host 0.0.0.0 --port 8000
