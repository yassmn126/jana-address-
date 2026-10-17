#!/usr/bin/env bash
# Print the index of the GPU with the most free memory (shared server: never take both).
# Usage: export CUDA_VISIBLE_DEVICES=$(bash gpu/pick_gpu.sh)
nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv,noheader,nounits \
  | awk -F', ' '{free=$3-$2; if (free>best) {best=free; idx=$1}} END {print idx}'
