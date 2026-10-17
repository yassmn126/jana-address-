# Jana Address — developer entry points. `make help` lists them.
SHELL := /bin/bash
PY ?= python3
SAMPLE ?= data/sample_input.json
GOLD ?= data/sample_output.json

.PHONY: help up down logs test eval parse label-dry label dataset synth train export calibrate eval-dev images save-images offline-test speed web-dev api-dev

help:
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

up: ## docker compose up --build (API :8000, UI :3000)
	docker compose up --build -d && docker compose ps

down: ## stop containers
	docker compose down

logs: ## tail logs
	docker compose logs -f --tail=100

api-dev: ## run the API locally with reload (needs: pip install -r api/requirements-dev.txt)
	cd api && JANA_DATA_DIR=../data uvicorn jana_address.main:app --reload --port 8000

web-dev: ## run the React dev server (proxies /api → :8000)
	cd web && npm install && npm run dev

test: ## unit + golden + API tests
	cd api && $(PY) -m pytest -q

parse: ## parse $(SAMPLE) → out.json with the CLI
	cd api && $(PY) -m jana_address.service ../$(SAMPLE) ../out.json

eval: parse ## jury metric replica on $(GOLD) (set GOLD=data/dev_gold.json when the dev set exists)
	$(PY) -m eval.metric --pred out.json --gold $(GOLD) --errors errors.md

label-dry: ## run the labeling pipeline with the rule parser as labeler (no API key needed)
	$(PY) -m training.label_llm --input $(SAMPLE) --output data/labels_rules.jsonl --provider rules

label: ## LLM pre-labeling of data/train.json (needs ANTHROPIC_API_KEY; see training/README.md)
	$(PY) -m training.label_llm --input data/train.json --output data/labels_llm.jsonl --resume

dataset: ## labels + corrections → data/dataset (train/dev BIO, gold files, labels.json)
	$(PY) -m training.build_dataset --labels data/labels_llm.jsonl --corrections data/corrections.jsonl --only-verified --out data/dataset

synth: ## synthetic rows from the train split (never from dev)
	$(PY) -m training.synth --dataset data/dataset/train.jsonl --n 30000 --out data/dataset/synthetic.jsonl

train: ## fine-tune xlm-roberta-base (GPU); see training/README.md for options
	$(PY) -m training.train --train data/dataset/train.jsonl --train data/dataset/synthetic.jsonl --dev data/dataset/dev.jsonl --labels data/dataset/labels.json --model xlm-roberta-base --out runs/xlmr --epochs 5

export: ## checkpoint → api/artifacts/{model.onnx,tokenizer.json,labels.json} (+ ONNX≡PyTorch check)
	$(PY) -m training.export_onnx --checkpoint runs/xlmr --labels data/dataset/labels.json --check data/dataset/dev.jsonl

calibrate: ## confidence model on the dev gold → api/artifacts/calibrator.pkl (+ reliability plot)
	$(PY) -m training.calibrate --gold data/dataset/dev_gold.json --plot

eval-dev: ## jury metric on our dev gold (after `make dataset`)
	cd api && $(PY) -m jana_address.service ../data/dataset/dev_gold.json ../out.json
	$(PY) -m eval.metric --pred out.json --gold data/dataset/dev_gold.json --errors errors.md

speed: ## 1000-record timing through the running API (brief: ≤ 5 min)
	$(PY) -m eval.speed --input $(SAMPLE)

images: ## build both images for amd64 + arm64 (needs docker buildx)
	docker buildx build --platform linux/amd64,linux/arm64 -t jana-address-api:latest ./api --load || docker build -t jana-address-api:latest ./api
	docker buildx build --platform linux/amd64,linux/arm64 -t jana-address-web:latest ./web --load || docker build -t jana-address-web:latest ./web

save-images: ## export images for an offline jury machine: docker load -i jana-address-images.tar
	docker save jana-address-api:latest jana-address-web:latest -o jana-address-images.tar && ls -lh jana-address-images.tar

offline-test: ## start the stack with NO network and run the sample through it
	docker network create --internal jana-nonet 2>/dev/null || true
	docker run -d --rm --name jana-api-offline --network jana-nonet jana-address-api:latest
	sleep 8
	docker run --rm --network jana-nonet -v $$PWD/$(SAMPLE):/in.json:ro python:3.11-slim \
	  python -c "import json,urllib.request;d=open('/in.json','rb').read();r=urllib.request.Request('http://jana-api-offline:8000/api/v1/parse/batch',data=d,headers={'Content-Type':'application/json'});print(urllib.request.urlopen(r).read().decode()[:400])"
	docker stop jana-api-offline
