#!/usr/bin/env python3
"""
Verify pysradb's metadata is structurally equivalent to SRA Run Selector.

Hypothesis (from the user): pysradb returns the same data as a Run Selector
CSV download — only the column names differ. This test checks that for a
sample of runs by:

  1. Reading the reference CSV (manually downloaded from Run Selector).
  2. Running pysradb on the same accessions with detailed=True.
  3. Mapping pysradb columns → Run Selector columns (direct + derived).
  4. Comparing each cell. Reporting per-column match rate, gap columns
     (no pysradb source), and sample mismatches.

Exit 0 iff every column with a pysradb source matches the reference for
every sampled row. Exit 1 otherwise.

Usage:
  python3 tests/test_pysradb_runselector_format.py            # default: 5 rows
  python3 tests/test_pysradb_runselector_format.py --n 10
  python3 tests/test_pysradb_runselector_format.py --reference path/to/file.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

# Import the production mapping so this test verifies the same code path
# fetch_run_selector_metadata() uses.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from runselector_mapping import (  # noqa: E402
    DIRECT_MAP,
    DERIVED,
    GAPS,
    map_pysradb_row_to_runselector as map_pysradb_row,
)


def _column_type(rs_col: str) -> str:
    if rs_col in DIRECT_MAP:
        return "DIRECT"
    if rs_col in DERIVED:
        return "DERIVED"
    if rs_col in GAPS:
        return "GAP"
    return "UNMAPPED"


# ---- Test driver ----------------------------------------------------------
def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--reference",
                   default="/Users/yaoanleng/Downloads/SraRunTable-3.csv",
                   type=Path,
                   help="Run Selector CSV downloaded manually (default points at the user's example).")
    p.add_argument("--n", type=int, default=5,
                   help="Number of rows from the reference to test (default 5; keeps the test fast).")
    args = p.parse_args()

    if not args.reference.exists():
        print(f"ERROR: reference CSV not found: {args.reference}", file=sys.stderr)
        return 1

    with args.reference.open(newline="") as f:
        reader = csv.DictReader(f)
        rs_columns = reader.fieldnames or []
        ref_rows = list(reader)

    if not rs_columns:
        print("ERROR: reference CSV has no header.", file=sys.stderr)
        return 1

    sample = ref_rows[: args.n]
    accessions = [r["Run"] for r in sample if r.get("Run")]
    print(f"Reference: {args.reference}")
    print(f"Run Selector columns: {len(rs_columns)}  ({len(GAPS)} known gaps)")
    print(f"Sampling {len(sample)} run(s): {accessions}")
    print()

    # --- pysradb fetch
    try:
        from pysradb.sraweb import SRAweb  # type: ignore
    except ImportError:
        print("ERROR: pysradb is not installed (pip install pysradb).", file=sys.stderr)
        return 1

    db = SRAweb()
    pysradb_rows: dict[str, dict] = {}
    for acc in accessions:
        try:
            df = db.sra_metadata(acc, detailed=True)
        except Exception as e:
            print(f"  WARN: pysradb raised on {acc}: {e}")
            continue
        if df is None or len(df) == 0:
            print(f"  WARN: no pysradb data for {acc}")
            continue
        if "run_accession" in df.columns:
            df = df[df["run_accession"] == acc]
        if len(df) == 0:
            print(f"  WARN: no pysradb row matched {acc}")
            continue
        pysradb_rows[acc] = df.iloc[0].to_dict()

    if not pysradb_rows:
        print("ERROR: pysradb returned nothing for the sample.", file=sys.stderr)
        return 1

    # --- Compare per column
    print("=" * 88)
    print(f"{'Run Selector column':25s}  {'Type':8s}  {'Match':>5s} / {'Total':5s}  Notes")
    print("-" * 88)

    column_stats: dict[str, tuple[int, int, str]] = {}
    sample_mismatches: dict[str, list[tuple[str, str, str]]] = {}

    for rs_col in rs_columns:
        ctype = _column_type(rs_col)
        matches = 0
        total = 0
        for acc in accessions:
            if acc not in pysradb_rows:
                continue
            ref_row = next((r for r in sample if r["Run"] == acc), None)
            if ref_row is None:
                continue
            mapped = map_pysradb_row(pysradb_rows[acc])
            ref_val = (ref_row.get(rs_col) or "").strip()
            got_val = (mapped.get(rs_col) or "").strip()
            total += 1
            if ref_val == got_val:
                matches += 1
            elif ctype != "GAP":
                sample_mismatches.setdefault(rs_col, []).append((acc, ref_val, got_val))
        column_stats[rs_col] = (matches, total, ctype)
        notes = ""
        if ctype == "GAP":
            notes = GAPS.get(rs_col, "no pysradb source")
        elif total > 0 and matches < total and rs_col in sample_mismatches:
            ex_acc, ex_ref, ex_got = sample_mismatches[rs_col][0]
            notes = f"e.g. {ex_acc}: ref={ex_ref!r} got={ex_got!r}"
        print(f"{rs_col:25s}  {ctype:8s}  {matches:>5d} / {total:5d}  {notes}")

    print("-" * 88)
    direct_or_derived = [c for c, (_, _, ct) in column_stats.items() if ct in ("DIRECT", "DERIVED")]
    perfect = [c for c in direct_or_derived if column_stats[c][0] == column_stats[c][1] and column_stats[c][1] > 0]
    failed = [c for c in direct_or_derived if column_stats[c][1] > 0 and column_stats[c][0] < column_stats[c][1]]
    unmapped = [c for c, (_, _, ct) in column_stats.items() if ct == "UNMAPPED"]

    print(f"PASS columns (DIRECT/DERIVED, every sampled row matched): {len(perfect)} / {len(direct_or_derived)}")
    print(f"FAIL columns (DIRECT/DERIVED, some sampled rows differed):  {len(failed)}")
    print(f"GAP  columns (no pysradb source — left blank):              {len(GAPS)}")
    print(f"UNMAPPED columns (in reference, not in our schema):         {len(unmapped)}")

    if unmapped:
        print(f"  unmapped: {unmapped}")
    if failed:
        print()
        print("Mismatch details:")
        for c in failed:
            for acc, ref, got in sample_mismatches[c][:3]:
                print(f"  {c} | {acc}: ref={ref!r} got={got!r}")

    print()
    if failed or unmapped:
        print("RESULT: FAIL — at least one derivable column does not match the reference.")
        return 1
    print("RESULT: PASS — every direct/derived column matches the reference for the sampled rows.")
    print(f"Hypothesis (pysradb is Run Selector minus column names) is true for {len(perfect)} of {len(rs_columns)} columns; {len(GAPS)} require a non-pysradb source.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
