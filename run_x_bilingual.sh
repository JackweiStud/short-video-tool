#!/usr/bin/env bash
set -euo pipefail
ROOT=/workspace/short-video-tool
VENV=/workspace/venvs/short-video-tool
OUT_ROOT=/workspace/short-video-tool/output
cd "$ROOT"
# Load LLM key from secure store if available
if python3 - <<'PY'
import json
from pathlib import Path
p=Path('/home/box/sand-data/box-secrets.json')
d=json.loads(p.read_text()) if p.exists() else {}
k=(d.get('card') or {}).get('LLM_API_KEY') or (d.get('card') or {}).get('SILICONFLOW_API_KEY')
raise SystemExit(0 if k else 1)
PY
then
  export LLM_API_KEY="$(python3 - <<'PY'
import json
from pathlib import Path
d=json.loads(Path('/home/box/sand-data/box-secrets.json').read_text())['card']
print(d.get('LLM_API_KEY') or d.get('SILICONFLOW_API_KEY'))
PY
)"
fi
# Bot PC: video summary needs longer read timeout than legacy 60s default
export LLM_TIMEOUT="${LLM_TIMEOUT:-180}"
URL="${1:?usage: run_x_bilingual.sh <x-or-video-url> [output-subdir]}"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="${2:-$OUT_ROOT/x-$STAMP}"
mkdir -p "$OUT"
exec "$VENV/bin/python" main.py \
  --url "$URL" \
  --output "$OUT" \
  --no-clip \
  --burn-subtitles \
  --subtitle-status none \
  --summary \
  --quality best
