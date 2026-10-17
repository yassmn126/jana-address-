# artifacts/

Build-time artifacts copied into the API image:

- `model.onnx`, `tokenizer.json`, `labels.json` — the token tagger (from `training/export_onnx.py`)
- `gazetteer.sqlite` — OSM + KATO gazetteer (from `data_pipeline/`)
- `calibrator.pkl` — confidence model (from `training/calibrate.py`)

Nothing here yet: the service runs on rules + the YAML seed gazetteer until these land.
Large binaries should be tracked with Git LFS or built in CI, not committed raw.
