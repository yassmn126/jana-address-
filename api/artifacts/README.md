# artifacts/

Copied into the API image at build time; the service detects each file on start-up (`GET /version`).

| file | built by | contents | committed? |
|---|---|---|---|
| `gazetteer.sqlite` | `make gazetteer` (`data_pipeline/build_gazetteer.py`) | OSM Kazakhstan: settlements, city districts, микрорайоны/ЖК, streets, house→district | yes (~16 MB, OSM only) |
| `model.onnx` + `model_meta.json` + `labels.json` | `make train-charcrf` (`training/train_charcrf.py`) | char-CNN+BiLSTM+CRF tagger, vocabularies, CRF transitions | yes while trained on OSM/synthetic only |
| `arbiter_policy.json` | `make tune` | rules ⊕ model thresholds tuned on the jury metric | yes |
| `calibrator.json` (+ `.png`) | `make calibrate` | logistic regression + isotonic table for `confidence` | yes |
| `tokenizer.json` | `training/export_onnx.py` | only for an XLM-R export (GPU path) | no |

Without any of them the service still runs: rules + the YAML seed gazetteer + heuristic confidence.
Data policy for artifacts retrained on `train.json`: docs/ML.md §5.
OpenStreetMap data © OpenStreetMap contributors, available under the Open Database License (ODbL).
