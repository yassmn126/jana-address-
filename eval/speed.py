"""Timing check against a running API: 1000 records must finish well under the brief's 5 minutes.

    python -m eval.speed [--url http://localhost:8000] [--input data/sample_input.json] [--n 1000]
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.request


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--input", default="data/sample_input.json")
    ap.add_argument("--n", type=int, default=1000)
    a = ap.parse_args(argv)
    with open(a.input, encoding="utf-8") as f:
        base = json.load(f)
    if isinstance(base, dict) and "records" in base:
        base = base["records"]
    recs = (base * (a.n // max(1, len(base)) + 1))[: a.n]
    recs = [{"id": f"t{i}", "raw_address": r.get("raw_address", "")} for i, r in enumerate(recs)]
    req = urllib.request.Request(f"{a.url}/api/v1/parse/batch", data=json.dumps(recs).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    out = json.load(urllib.request.urlopen(req, timeout=600))
    dt = time.perf_counter() - t0
    print(f"{len(out)} records in {dt:.2f}s  ({1000*dt/len(out):.1f} ms/record)  — limit: 300 s per 1000")
    return 0 if dt < 300 else 1


if __name__ == "__main__":
    raise SystemExit(main())
