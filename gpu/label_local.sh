#!/usr/bin/env bash
# Run ON the server. Starts a local OpenAI-compatible LLM for labeling (data never leaves the university).
#   bash gpu/label_local.sh                       # Qwen2.5-14B-Instruct-AWQ (~12 GB VRAM) on the least-busy GPU, port 8001
#   LLM=Qwen/Qwen2.5-7B-Instruct bash gpu/label_local.sh     # smaller (~15 GB fp16) if AWQ kernels misbehave
# then:
#   OPENAI_BASE_URL=http://127.0.0.1:8001/v1 OPENAI_API_KEY=none python -m training.label_llm \
#       --input data/train.json --output data/labels_llm.jsonl --provider openai-compatible --model $LLM --limit 100
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source ../.venv/bin/activate 2>/dev/null || source .venv/bin/activate
LLM="${LLM:-Qwen/Qwen2.5-14B-Instruct-AWQ}"
PORT="${PORT:-8001}"
GPU="${GPU:-$(bash gpu/pick_gpu.sh)}"
export CUDA_VISIBLE_DEVICES="$GPU"
python -c "import vllm" 2>/dev/null || pip install -q vllm        # large install (several GB), once
echo "vLLM: $LLM on GPU $GPU, port $PORT (Ctrl-C to stop; or run inside tmux)"
exec vllm serve "$LLM" --port "$PORT" --max-model-len 8192 --gpu-memory-utilization 0.55 --dtype auto --disable-log-requests
