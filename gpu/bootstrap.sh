#!/usr/bin/env bash
# Run ON the GPU server as the normal user. Idempotent, no sudo, nothing system-wide.
# 1) environment report  2) ~/hackathon-ml + venv  3) CUDA-enabled torch  4) repo + deps + tests
set -uo pipefail
WS="${WS:-$HOME/hackathon-ml}"
REPO_URL="${REPO_URL:-https://github.com/yassmn126/jana-address-.git}"

line() { printf '\n== %s ==\n' "$*"; }

line identity
hostname; whoami; pwd; uname -m; (. /etc/os-release && echo "$PRETTY_NAME")
[ "$(hostname)" = "univer-gpu6" ] || echo "WARNING: not on univer-gpu6 — are you on the Mac?"

line gpu
nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu --format=csv
echo "-- other processes on the GPUs (do not touch) --"
nvidia-smi --query-compute-apps=gpu_uuid,pid,process_name,used_memory --format=csv 2>/dev/null || true

line tools
python3 --version
for t in conda uv srun sbatch tmux screen git rsync nvcc; do printf '%-7s %s\n' "$t" "$(command -v "$t" 2>/dev/null || echo -)"; done

line disk
df -h "$HOME" | tail -1
du -sh "$HOME" 2>/dev/null | tail -1

line workspace
mkdir -p "$WS" && cd "$WS"
if [ ! -d .venv ]; then
  python3 -m venv .venv || { echo "python3 -m venv failed → try: python3 -m pip install --user virtualenv && python3 -m virtualenv .venv"; exit 1; }
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install -q --upgrade pip wheel

line torch
if ! python -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)" 2>/dev/null; then
  # driver 595 (CUDA 13.2) runs any current wheel; newest index first, fall back if a wheel is missing
  for idx in cu130 cu128 cu126; do
    echo "trying torch wheels: $idx"
    if pip install -q torch --index-url "https://download.pytorch.org/whl/$idx" \
       && python -c "import torch, sys; sys.exit(0 if torch.cuda.is_available() else 1)"; then
      break
    fi
  done
fi
python - <<'EOF'
import torch
print("torch", torch.__version__, "| cuda build", torch.version.cuda, "| devices", torch.cuda.device_count())
for i in range(torch.cuda.device_count()):
    free, total = torch.cuda.mem_get_info(i)
    print(f"  gpu{i}: {torch.cuda.get_device_name(i)}  free {free/2**30:.1f} / {total/2**30:.1f} GiB")
x = torch.randn(2048, 2048, device="cuda")
print("cuda matmul ok:", float((x @ x).sum()) != 0.0)
EOF

line repo
cd "$WS"
if [ ! -d jana-address- ]; then git clone "$REPO_URL" jana-address-; fi
cd jana-address- && git pull --ff-only 2>/dev/null || true
pip install -q -r api/requirements-dev.txt -r training/requirements.txt
(cd api && python -m pytest -q 2>&1 | tail -3)

line summary
echo "workspace: $WS  (venv: $WS/.venv)"
echo "repo:      $WS/jana-address-  @ $(git rev-parse --short HEAD)"
echo "next:      from the Mac run  bash gpu/sync.sh  to copy data/train.json, then  bash gpu/train_remote.sh  here"
