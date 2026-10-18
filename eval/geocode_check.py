"""End-to-end geocoding check: real OSM addresses written the way people type them → parse → geocode → distance.

    python -m eval.geocode_check [--n 500] [--seed 3] [--md runs/geocode.md]

Samples (city, street, house) triples from the gazetteer's house points in cities and towns, renders each as a string
in one of several styles («г. Алматы, ул. Толе би 286/5, кв 12», «Толе би 286/5 Алматы», «алматы толе би д.286/5»,
Kazakh word order, …), runs the full service and the geocoder, and reports the precision mix (house / street / … /
none) and the distance from the true OSM point. Because the points come from the same gazetteer, this measures the
PARSING + MATCHING chain (does a messy string still reach the right building?), not OSM's own accuracy.
"""
from __future__ import annotations

import argparse
import math
import random
import sqlite3
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "api"))

from jana_address.service import parse_one  # noqa: E402

STYLES = [
    lambda c, t, b, h: f"г. {c}, {t} {b} {h}, кв {random.randint(1, 200)}",
    lambda c, t, b, h: f"{c}, {b} {h}",
    lambda c, t, b, h: f"{b} {h} {c}",
    lambda c, t, b, h: f"{c.lower()} {b.lower()} д.{h}",
    lambda c, t, b, h: f"{c}, {t} {b}, дом {h}, {random.randint(1, 9)} подъезд",
    lambda c, t, b, h: f"{c.upper()}, {t.upper()} {b.upper()} {h}",
    lambda c, t, b, h: f"{c}, {b} көшесі {h}, {random.randint(1, 99)} пәтер" if t == "ул." else f"{c}, {t} {b} {h}",
]
TYPE_ABBR = {"улица": "ул.", "проспект": "пр.", "переулок": "пер.", "микрорайон": "мкр", "бульвар": "б-р", None: ""}


def km(a, b):
    dlat = (a[0] - b[0]) * 111.2
    dlon = (a[1] - b[1]) * 111.2 * math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(dlat, dlon)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--md")
    a = ap.parse_args(argv)
    random.seed(a.seed)
    con = sqlite3.connect(f"file:{ROOT / 'api/artifacts/gazetteer.sqlite'}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT s.name, st.type, st.base, hp.house_norm, hp.lat_e5, hp.lon_e5 FROM house_points hp "
        "JOIN streets st ON st.id = hp.street_id JOIN settlements s ON s.id = st.settlement_id "
        "WHERE s.place IN ('city','town') AND hp.house_norm GLOB '[0-9]*' AND length(st.base) BETWEEN 3 AND 30 "
        "ORDER BY RANDOM() LIMIT ?", (a.n * 3,)).fetchall()
    # stratify: at most 40 % from one city so Алматы / Шымкент do not dominate
    per_city, sample = Counter(), []
    for r in rows:
        if per_city[r[0]] < 0.4 * a.n and len(sample) < a.n:
            per_city[r[0]] += 1
            sample.append(r)
    prec, dists, misses = Counter(), [], []
    for i, (city, typ, base, house, la, lo) in enumerate(sample):
        raw = random.choice(STYLES)(city, TYPE_ABBR.get(typ, ""), base, house).replace("  ", " ").strip(" ,")
        out = parse_one(f"g{i}", raw, explain=True)
        geo = out.get("geo")
        p = geo["precision"] if geo else "none"
        prec[p] += 1
        if geo:
            d = km((geo["lat"], geo["lon"]), (la / 1e5, lo / 1e5))
            dists.append(d)
            if p != "house" and len(misses) < 15:
                misses.append((raw, p, out.get("street_name"), out.get("house"), round(d, 2)))
        elif len(misses) < 15:
            misses.append((raw, "none", out.get("street_name"), out.get("house"), None))
    n = len(sample)
    lines = [f"# Geocoding check — {n} real OSM addresses from {len(per_city)} cities, typed in 7 styles", "",
             "| precision | share |", "|---|---|"]
    for k in ("house", "house_near", "street", "microdistrict", "district", "locality", "none"):
        if prec[k]:
            lines.append(f"| {k} | {100 * prec[k] / n:.1f} % |")
    if dists:
        within = lambda r: 100 * sum(d <= r for d in dists) / n
        lines += ["", f"Distance to the true point: median {1000 * statistics.median(dists):.0f} m; "
                      f"within 50 m {within(0.05):.1f} %, 500 m {within(0.5):.1f} %, 2 km {within(2):.1f} % of all records.", "",
                  "## Not resolved to the house (first 15)", "", "| string | precision | street | house | km |", "|---|---|---|---|---|"]
        lines += [f"| {r} | {p} | {s} | {h} | {d} |" for r, p, s, h, d in misses]
    text = "\n".join(lines)
    print(text)
    if a.md:
        Path(a.md).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
