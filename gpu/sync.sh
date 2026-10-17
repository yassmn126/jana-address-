#!/usr/bin/env bash
# Run ON THE MAC (VPN on). Copies the working tree + data to the server, pulls artifacts back.
#   bash gpu/sync.sh            # push code + data
#   bash gpu/sync.sh pull       # pull api/artifacts (model.onnx, tokenizer.json, labels.json, calibrator.pkl) + runs/*.log
set -euo pipefail
HOST="${HOST:-rishat@192.168.124.26}"
REMOTE="${REMOTE:-~/hackathon-ml/jana-address-}"
LOCAL="$(cd "$(dirname "$0")/.." && pwd)"
EXCLUDES=(--exclude .git --exclude node_modules --exclude web/dist --exclude .venv --exclude __pycache__ --exclude .pytest_cache --exclude runs --exclude 'api/artifacts/*.onnx')
if [ "${1:-push}" = "pull" ]; then
  rsync -avz --progress "$HOST:$REMOTE/api/artifacts/" "$LOCAL/api/artifacts/"
  rsync -avz --progress "$HOST:$REMOTE/runs/*.log" "$LOCAL/runs/" 2>/dev/null || true
  rsync -avz --progress "$HOST:$REMOTE/data/dataset/" "$LOCAL/data/dataset/" 2>/dev/null || true
else
  rsync -avz --progress "${EXCLUDES[@]}" "$LOCAL/" "$HOST:$REMOTE/"
fi
