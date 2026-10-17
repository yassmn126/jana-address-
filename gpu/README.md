# gpu/ — working on the university server (univer-gpu6, 2 × A10 23 GB, shared)

The server sits behind the Kerio VPN, so everything here runs **from your Mac terminal** (VPN on),
not from a cloud agent. Order of operations:

```bash
# 0. on the Mac: VPN connected, then verify the host key fingerprint before trusting it
ssh rishat@192.168.124.26          # expected ED25519 SHA256:ace9t/xEFi6L5f5r2RGHezrdIgPOEWFHtji5M8AG3UU (still to be confirmed by IT)

# 1. on the server: environment check + workspace + CUDA torch + repo + tests (idempotent, no sudo)
curl -fsSL https://raw.githubusercontent.com/yassmn126/jana-address-/main/gpu/bootstrap.sh | bash
#    (or: git clone https://github.com/yassmn126/jana-address-.git ~/hackathon-ml/jana-address- && bash ~/hackathon-ml/jana-address-/gpu/bootstrap.sh)
#    → paste the printed summary back into the chat before any training.

# 2. on the Mac: ship the data (never commit it)
bash gpu/sync.sh                   # rsync code + data/train.json + labels → ~/hackathon-ml/jana-address-/

# 3. on the server: labeling with a local model (no data leaves the university network)
bash gpu/label_local.sh            # starts vLLM (Qwen2.5-14B-Instruct-AWQ) on the least-busy GPU, port 8001
OPENAI_BASE_URL=http://127.0.0.1:8001/v1 OPENAI_API_KEY=none \
python -m training.label_llm --input data/train.json --output data/labels_llm.jsonl --provider openai-compatible --model Qwen/Qwen2.5-14B-Instruct-AWQ --limit 100

# 4. on the server: train in tmux on the least-busy GPU, logs in runs/
bash gpu/train_remote.sh           # = make dataset/synth + training.train + export_onnx + calibrate
tmux attach -t jana-train          # watch; Ctrl-B D to detach
```

Rules (shared machine): no sudo, never touch other users' processes, one GPU only
(`CUDA_VISIBLE_DEVICES` is set by the scripts to the GPU with the most free memory), ask before
anything that runs longer than ~30 min. XLM-R base fine-tuning needs ~6–8 GB VRAM; vLLM 14B-AWQ
needs ~12 GB — both fit next to a moderate neighbour, neither fits if a neighbour holds > 15 GB.

Passwords are typed at the SSH prompt, never pasted into chats, files or the repo. Rotate the
password after it has been shared anywhere (`passwd` on the server).
