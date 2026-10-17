# Jana Address — developer entry points. `make help` lists them.
SHELL := /bin/bash
PY ?= python3
SAMPLE ?= data/sample_input.json
GOLD ?= data/sample_output.json

.PHONY: help up down logs test eval parse label-dry label dataset synth train export calibrate eval-dev images save-images offline-test speed web-dev api-dev \
        osm gazetteer synth-grammar train-charcrf ml-cold-start ab pseudo-label finetune-real tune offline-check

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

calibrate: ## confidence model on the dev gold → api/artifacts/calibrator.json (+ reliability plot)
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

offline-check: ## build the API image, run it with --network none: start time, schema, jury metric, 1000-record speed, memory
	docker compose build api && bash scripts/offline_check.sh

offline-test: ## start the stack with NO network and run the sample through it
	docker network create --internal jana-nonet 2>/dev/null || true
	docker run -d --rm --name jana-api-offline --network jana-nonet jana-address-api:latest
	sleep 8
	docker run --rm --network jana-nonet -v $$PWD/$(SAMPLE):/in.json:ro python:3.11-slim \
	  python -c "import json,urllib.request;d=open('/in.json','rb').read();r=urllib.request.Request('http://jana-api-offline:8000/api/v1/parse/batch',data=d,headers={'Content-Type':'application/json'});print(urllib.request.urlopen(r).read().decode()[:400])"
	docker stop jana-api-offline

# ------------------------------------------------------------------ ML track without train.json (cold start)
OSM_URL ?= https://download.geofabrik.de/asia/kazakhstan-latest.osm.pbf
SYNTH_N ?= 150000
GOLD_SETS ?= --gold data/dev_handmade.json --gold data/conventions_examples.json

osm: ## download the OSM Kazakhstan extract (225 MB, resumable) → data/osm/
	mkdir -p data/osm && curl -L -C - --retry 10 -o data/osm/kazakhstan-latest.osm.pbf $(OSM_URL)

gazetteer: ## OSM → api/artifacts/gazetteer.sqlite (~90 s): settlements, districts, микрорайоны/ЖК, streets, houses
	$(PY) -m data_pipeline.build_gazetteer --pbf data/osm/kazakhstan-latest.osm.pbf --out api/artifacts/gazetteer.sqlite

synth-grammar: ## OSM-backed synthetic addresses: train (seed 1) + held-out dev (seed 2)
	$(PY) -m training.synth_grammar --n $(SYNTH_N) --seed 1 --out data/dataset/synth_grammar.jsonl --id-prefix sg
	$(PY) -m training.synth_grammar --n 5000 --seed 2 --out data/dataset/synth_dev.jsonl --gold-out data/dataset/synth_dev_gold.json --id-prefix sd

train-charcrf: ## char-CNN+BiLSTM+CRF on synthetic data (CPU, ~1 h) → api/artifacts/model.onnx + model_meta.json
	$(PY) -m training.train_charcrf --train data/dataset/synth_grammar.jsonl --dev data/dataset/synth_dev.jsonl \
	  --test data/dev_handmade.json --test data/conventions_examples.json --out runs/charcrf --export api/artifacts --epochs 8

tune: ## grid-search the arbiter thresholds → api/artifacts/arbiter_policy.json
	$(PY) -m training.tune_arbiter $(GOLD_SETS) --gold data/dataset/synth_dev_gold.json:0.3

ab: ## rules vs model vs hybrid, jury metric per gold file
	$(PY) -m eval.ab $(GOLD_SETS) --gold data/dataset/synth_dev_gold.json --errors-dir runs/ab --json runs/ab/ab.json

ml-cold-start: gazetteer synth-grammar train-charcrf tune ## everything above + calibration + A/B, no train.json needed
	$(PY) -m training.calibrate --gold data/dataset/synth_dev_gold.json --gold data/dev_handmade.json:10 --plot
	$(MAKE) ab test

# ------------------------------------------------------------------ when train.json arrives
pseudo-label: ## offline silver labels (rules ⊕ model agree) + review queue for the UI → data/labels_pseudo.jsonl
	$(PY) -m training.pseudo_label --input data/train.json --output data/labels_pseudo.jsonl

finetune-real: ## real labels → dataset → fine-tune the synthetic-pretrained tagger (warm start) → export
	$(PY) -m training.build_dataset --labels data/labels_pseudo.jsonl --corrections data/corrections.jsonl --only-verified --out data/dataset
	$(PY) -m training.train_charcrf --init runs/charcrf/model.pt --train data/dataset/train.jsonl \
	  --train data/dataset/synth_grammar.jsonl --max-train 60000 --real-weight 5 --dev data/dataset/dev.jsonl \
	  --test data/dev_handmade.json --test data/conventions_examples.json --out runs/charcrf_real --export api/artifacts --epochs 6 --lr 1e-3
	$(PY) -m training.calibrate --gold data/dataset/dev_gold.json --plot
	$(PY) -m eval.ab --gold data/dataset/dev_gold.json $(GOLD_SETS) --errors-dir runs/ab
