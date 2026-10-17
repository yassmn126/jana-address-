"""Gazetteer (OSM + seed), name keys, char-CRF decoding and synthetic-data integrity.

OSM-backed checks are skipped when api/artifacts/gazetteer.sqlite is absent (fresh clone without the
artifact); everything else runs with the runtime dependencies only (no torch).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from jana_address.gazetteer.names import base_of_numbered, norm, split_district, split_living, split_street  # noqa: E402
from jana_address.gazetteer.osm import Gazetteer  # noqa: E402
from jana_address.ml.charcrf import bio_mask, marginals, viterbi  # noqa: E402

GZ_PATH = ROOT / "api" / "artifacts" / "gazetteer.sqlite"
needs_osm = pytest.mark.skipif(not GZ_PATH.exists(), reason="gazetteer.sqlite not built (make gazetteer)")


# ------------------------------------------------------------------ name keys
@pytest.mark.parametrize("a,b", [("Сығанақ", "Сыганак"), ("Самал-2", "Самал 2"), ("Самал2", "самал-2"),
                                 ("Мамыр 4-й", "Мамыр-4"), ("улица имени Абая", "улица Абая"), ("Ауэзова", "Ауезова")])
def test_norm_equivalences(a, b):
    assert norm(a) == norm(b)


@pytest.mark.parametrize("name,expected", [
    ("улица Абая", ("улица", "Абая")), ("Абай даңғылы", ("проспект", "Абай")),
    ("Рыбинский переулок", ("переулок", "Рыбинский")), ("Ш. Уалиханова", (None, "Ш. Уалиханова")),
    ("Жібек жолы көшесі", ("улица", "Жібек жолы")),
])
def test_split_street(name, expected):
    assert split_street(name) == expected


def test_split_district_and_living():
    assert split_district("район Есиль")[0] == "Есиль"
    assert split_district("Есіл ауданы")[0] == "Есіл"
    assert split_district("район имени Казыбек би")[0] == "Казыбек би"
    assert split_living("Томирис тұрғын үй кешені") == ("ЖК", "Томирис")
    assert split_living("мкр Самал-2") == ("микрорайон", "Самал-2")
    assert split_living('"Falcon Сity" ТҮК') == ("ЖК", "Falcon Сity")
    assert base_of_numbered("Аксай 3а") == "Аксай" and base_of_numbered("Самал-2") == "Самал"


# ------------------------------------------------------------------ gazetteer
def test_seed_only_gazetteer_still_answers():
    gz = Gazetteer(path=ROOT / "nonexistent.sqlite")
    assert gz.kind == "seed"
    assert gz.province_of_city("Актобе")[0] == "Актюбинская область"
    assert gz.city_of_district("Турксибский")[0] == "Алматы"
    assert gz.district_of_microdistrict("Алматы", "Аксай 3а")[0] == "Ауэзовский"


@needs_osm
def test_osm_gazetteer_lookups():
    gz = Gazetteer(path=GZ_PATH)
    assert gz.kind == "osm+seed"
    assert gz.city_ru("Орал")[0] == "Уральск"
    assert gz.city_ru("Өскемен")[0] == "Усть-Каменогорск"
    assert gz.city_ru("Алматы") == (None, None)                     # same in both languages → as written
    assert gz.province_of_city("Хромтау")[0] == "Актюбинская область"
    assert gz.city_of_district("Медеуский")[0] == "Алматы"
    assert gz.district_of_microdistrict("Алматы", "Аксай-4")[0] == "Ауэзовский"
    d, src = gz.district_of_street("Алматы", "Толе би", "286/5")
    assert d in {"Алмалинский", "Ауэзовский", "Бостандыкский", "Жетысуский", "Наурызбайский"} and src.startswith("gazetteer:osm")
    assert gz.street_exists("Астана", "Кенесары")


@needs_osm
def test_enrichment_never_overwrites_the_string():
    from jana_address.service import parse_one
    out = parse_one("x", "Алматы, Медеуский район, ул. Кунаева 21Б", explain=True)
    assert out["district"] == "Медеуский" and out["provenance"]["district"] == "string"
    out = parse_one("x", "Алматы, мкр Аксай-4, 98, кв 21", explain=True)
    assert out["district"] == "Ауэзовский" and out["provenance"]["district"].startswith("gazetteer")


# ------------------------------------------------------------------ CRF decoding
LABELS = ["O", "B-HOUSE", "I-HOUSE", "B-STREET_NAME", "I-STREET_NAME"]


def test_viterbi_respects_bio_constraints():
    allowed, allowed_start = bio_mask(LABELS)
    T, L = 4, len(LABELS)
    em = np.zeros((T, L))
    em[:, 2] = 5.0                       # emissions love I-HOUSE everywhere: illegal at t=0 and after O
    path = viterbi(em, np.zeros((L, L)), np.zeros(L), np.zeros(L), allowed, allowed_start)
    labs = [LABELS[j] for j in path]
    assert labs[0] != "I-HOUSE"
    for prev, cur in zip(labs, labs[1:]):
        if cur.startswith("I-"):
            assert prev[2:] == cur[2:] and prev != "O"


def test_marginals_are_distributions():
    rng = np.random.default_rng(0)
    allowed, allowed_start = bio_mask(LABELS)
    em = rng.normal(size=(6, len(LABELS)))
    m = marginals(em, rng.normal(size=(5, 5)), np.zeros(5), np.zeros(5), allowed, allowed_start)
    assert np.allclose(m.sum(1), 1.0, atol=1e-6)
    assert m[0, 2] < 1e-3 and m[0, 4] < 1e-3          # I- can't start a sequence


# ------------------------------------------------------------------ synthetic generator
def test_synth_grammar_rows_are_consistent(tmp_path):
    from training.validate_labels import to_bio
    out, gold = tmp_path / "s.jsonl", tmp_path / "g.json"
    r = subprocess.run([sys.executable, "-m", "training.synth_grammar", "--n", "300", "--out", str(out),
                        "--gold-out", str(gold), "--seed", "5"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 300
    for row in rows:
        spans, cur = {}, None
        for t, l in zip(row["tokens"], row["labels"]):
            if l == "O":
                cur = None
                continue
            f = l[2:].lower()
            if l.startswith("B-") or cur != f:
                spans[f] = [t["start"], t["end"]]
            else:
                spans[f][1] = t["end"]
            cur = f
        assert to_bio(row["raw"], {k: tuple(v) for k, v in spans.items()},
                      [(t["text"], t["start"], t["end"]) for t in row["tokens"]]) == row["labels"]
        g = row["fields"]
        # copy-not-rewrite: every string-derived gold value is in the raw string (except a Kazakh city
        # mapped to its Russian name and «12 корпус 2» → «12/2»)
        for f in ("street_name", "apartment", "entrance", "floor", "landmark", "district", "living_area"):
            if g.get(f):
                assert norm(g[f]) in norm(row["raw"]), (f, g[f], row["raw"])


def test_calibrator_json_roundtrip(tmp_path, monkeypatch):
    """A calibrator.json with the current feature list is picked up; a stale one is ignored."""
    from jana_address.confidence import heuristic
    from jana_address.confidence.features import FEATURE_NAMES, VERSION
    n = len(FEATURE_NAMES)
    cal = {"version": VERSION, "features": FEATURE_NAMES, "mean": [0.0] * n, "scale": [1.0] * n, "coef": [0.0] * n,
           "intercept": 2.0, "iso_x": [0.0, 1.0], "iso_y": [0.0, 1.0]}
    monkeypatch.setattr(heuristic, "_CAL", cal)
    monkeypatch.setattr(heuristic, "_CAL_TRIED", True)
    p = heuristic.calibrated_confidence([], {}, None, 5, "x")
    assert abs(p - 0.88) < 0.01                       # sigmoid(2) ≈ 0.881
    monkeypatch.setattr(heuristic, "_CAL", {**cal, "version": VERSION - 1})
    assert heuristic.calibrated_confidence([], {}, None, 5, "x") is None
