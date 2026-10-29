"""Verify archived file hashes and a few independent financial identities.

This command never edits or downloads files. Run from any working directory.
"""
import csv
import hashlib
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main():
    checked = 0
    for line in (ROOT / "manifest.sha256").read_text(encoding="utf-8").splitlines():
        expected, relative = line.split("  ", 1)
        path = ROOT / relative
        if not path.is_file():
            raise ValueError("Missing file: " + relative)
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Changed file: " + relative)
        checked += 1
    manifest = json.loads((ROOT / "configs/experiments.json").read_text(encoding="utf-8"))
    data = {}
    for e in manifest["experiments"]:
        d = json.loads((ROOT/e["raw_path"]).read_text(encoding="utf-8"))
        data[e["id"]] = d
        stats = d["statistics"]
        initial, final = float(stats["Start Equity"]), float(stats["End Equity"])
        reported = float(stats["Net Profit"].strip("%"))/100
        if not math.isclose(final/initial-1, reported, abs_tol=.0000051):
            raise ValueError("Equity/return identity failed: " + e["id"])
        params = json.loads((ROOT/e["parameter_path"]).read_text(encoding="utf-8"))
        if params != d["algorithmConfiguration"]["parameters"]:
            raise ValueError("Parameter snapshot differs from raw export: " + e["id"])
    duplicate = manifest["duplicate_exports"][0]
    if (ROOT/duplicate["raw_path"]).read_bytes() != (ROOT/duplicate["duplicate_of"]).read_bytes():
        raise ValueError("Expected byte-identical duplicate export")
    original = data["model_interaction"]
    repeat = data["diagnostic_C"]
    if original["statistics"] != repeat["statistics"]:
        raise ValueError("Interaction repeat changed portfolio statistics")
    for name in ("Allocation", "Strategy Equity"):
        if original["charts"][name] != repeat["charts"][name]:
            raise ValueError("Interaction repeat changed chart: " + name)
    with (ROOT / "results/summary.csv").open(encoding="utf-8", newline="") as handle:
        summary = list(csv.DictReader(handle))
    if len(summary) != 8 or any(int(row["allocation_count"]) != 72 for row in summary):
        raise ValueError("Unexpected summary dimensions")
    print(f"PASS: {checked} file hashes, 8 run identities, exact parameter snapshots, "
          "duplicate export and repeated-interaction consistency.")


if __name__ == "__main__":
    main()
