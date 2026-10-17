#!/usr/bin/env bash
# Run ON the server inside ~/hackathon-ml/jana-address-. Full ML pass on ONE GPU, inside tmux, logged.
#   bash gpu/train_remote.sh                # dataset → synth → train → export → calibrate
#   STAGES="train export" bash gpu/train_remote.sh
#   MODEL=microsoft/mdeberta-v3-base EPOCHS=6 BS=16 ACCUM=2 bash gpu/train_remote.sh
#   STAGES="pseudo charcrf-data charcrf calibrate" bash gpu/train_remote.sh   # char-CRF path (docs/ML.md):
#        offline silver labels → dataset → warm-start the synthetic-pretrained tagger on real rows (CUDA)
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source ../.venv/bin/activate 2>/dev/null || source .venv/bin/activate
GPU="${GPU:-$(bash gpu/pick_gpu.sh)}"
export CUDA_VISIBLE_DEVICES="$GPU"
MODEL="${MODEL:-xlm-roberta-base}"; EPOCHS="${EPOCHS:-5}"; BS="${BS:-32}"; ACCUM="${ACCUM:-1}"; SYN="${SYN:-30000}"
STAGES="${STAGES:-dataset synth train export calibrate}"
RUN="runs/$(date +%F_%H%M)_$(basename "$MODEL")"
mkdir -p runs
LOG="$RUN.log"
echo "GPU $GPU | model $MODEL | epochs $EPOCHS | bs $BS x accum $ACCUM | stages: $STAGES | log $LOG"
nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv,noheader | sed "s/^/  gpu /"

cmd=""
for s in $STAGES; do case $s in
  dataset)   cmd+="python -m training.build_dataset --labels data/labels_llm.jsonl $( [ -f data/corrections.jsonl ] && echo --corrections data/corrections.jsonl ) --out data/dataset && ";;
  synth)     cmd+="python -m training.synth --dataset data/dataset/train.jsonl --n $SYN --out data/dataset/synthetic.jsonl && ";;
  train)     cmd+="python -m training.train --train data/dataset/train.jsonl --train data/dataset/synthetic.jsonl --dev data/dataset/dev.jsonl --labels data/dataset/labels.json --model $MODEL --out $RUN --epochs $EPOCHS --bs $BS --grad-accum $ACCUM && ";;
  export)    cmd+="python -m training.export_onnx --checkpoint $RUN --labels data/dataset/labels.json --check data/dataset/dev.jsonl && ";;
  calibrate) cmd+="python -m training.calibrate --gold data/dataset/dev_gold.json --plot && ";;
  pseudo)    cmd+="python -m training.pseudo_label --input data/train.json --output data/labels_pseudo.jsonl && ";;
  charcrf-data) cmd+="python -m training.build_dataset --labels data/labels_pseudo.jsonl $( [ -f data/corrections.jsonl ] && echo --corrections data/corrections.jsonl ) --only-verified --out data/dataset && ";;
  charcrf)   cmd+="python -m training.train_charcrf --init runs/charcrf/model.pt --train data/dataset/train.jsonl --train data/dataset/synth_grammar.jsonl --max-train 60000 --real-weight 5 --dev data/dataset/dev.jsonl --test data/dev_handmade.json --test data/test_handmade.json --out $RUN-charcrf --export api/artifacts --epochs 6 --lr 1e-3 --device cuda && ";;
  ab)        cmd+="python -m eval.ab --gold data/dataset/dev_gold.json --gold data/dev_handmade.json --gold data/test_handmade.json --errors-dir $RUN-ab && ";;
  eval)      cmd+="(cd api && python -m jana_address.service ../data/dataset/dev_gold.json ../out.json) && python -m eval.metric --pred out.json --gold data/dataset/dev_gold.json --errors errors.md && ";;
esac; done
cmd+="echo ALL-DONE"

if command -v tmux >/dev/null; then
  tmux new-session -d -s jana-train "cd $(pwd) && source $VIRTUAL_ENV/bin/activate; export CUDA_VISIBLE_DEVICES=$GPU; ($cmd) 2>&1 | tee $LOG"
  echo "started in tmux session 'jana-train' → tmux attach -t jana-train   |   tail -f $LOG"
else
  nohup bash -c "export CUDA_VISIBLE_DEVICES=$GPU; ($cmd)" > "$LOG" 2>&1 &
  echo "started with nohup (pid $!) → tail -f $LOG"
fi
