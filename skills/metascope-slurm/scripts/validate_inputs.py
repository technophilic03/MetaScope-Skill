#!/usr/bin/env python3
"""
Validate the user's metadata CSV and write expanded_metadata.csv.

The CSV must be run-level — every row references an SRR/ERR/DRR run
accession. Non-run accessions (PRJNA, SRX, SRP, GSE, GSM) must be
expanded to runs *before* this script — see SKILL.md for how Claude does
that via NCBI E-utilities, or have the user download a metadata table
from NCBI Run Selector (https://www.ncbi.nlm.nih.gov/Traces/study/).

Two metadata formats are auto-detected from the header row:

  Standard          — sample_id, accession, library_layout, [sample_type, …]
  SRA Run Selector  — Run, LibraryLayout, [Sample Name | SampleName | BioSample], …
                      (download from https://www.ncbi.nlm.nih.gov/Traces/study/
                      OR raw E-utilities efetch runinfo output — both share
                      Run + LibraryLayout columns).

Mapping for the Run Selector / runinfo path:
  Run                                       → accession
  LibraryLayout                             → library_layout (lowercased)
  Sample Name | SampleName | BioSample | Run → sample_id (first non-empty;
                                              spaces normalized to underscores).

Outputs (when --output is given):
  expanded_metadata.csv with columns: sample_id, run_accession, library_layout

Surfaces every error at once. Exit 0 on success, 1 on validation failure.
"""
from __future__ import annotations  # makes type hints lazy → 3.7+ compatible

import argparse
import csv
import re
import sys
from pathlib import Path

# Only run accessions are accepted by this script. Non-run accessions
# (PRJNA, SRX, SRP, GSE, GSM, BioSample) must be expanded to runs at the
# SKILL.md / Claude layer (via WebFetch on NCBI E-utilities) before the CSV
# reaches this script.
RUN_PATTERN = re.compile(r"^[SED]RR\d+$")
SAMPLE_ID_PATTERN = re.compile(r"^\S+$")
LIBRARY_LAYOUTS = {"single", "paired"}

STANDARD_REQUIRED_COLS = {"sample_id", "accession", "library_layout"}
SRA_RUN_SELECTOR_REQUIRED_COLS = {"Run", "LibraryLayout"}

RUNSELECTOR_URL = "https://www.ncbi.nlm.nih.gov/Traces/study/"


def detect_metadata_format(fieldnames: list[str] | None) -> str | None:
    if not fieldnames:
        return None
    fn = set(fieldnames)
    if STANDARD_REQUIRED_COLS.issubset(fn):
        return "standard"
    if SRA_RUN_SELECTOR_REQUIRED_COLS.issubset(fn):
        return "sra_run_selector"
    return None


def normalize_sra_run_selector_row(raw: dict) -> dict:
    # "Sample Name" is the human-downloaded Run Selector spelling;
    # "SampleName" is what eutils efetch runinfo returns. Accept both so
    # raw eutils output drops in without renaming columns.
    sid_raw = (
        raw.get("Sample Name")
        or raw.get("SampleName")
        or raw.get("BioSample")
        or raw.get("Run")
        or ""
    ).strip()
    sid = re.sub(r"\s+", "_", sid_raw)
    accession = (raw.get("Run") or "").strip()
    layout = (raw.get("LibraryLayout") or "").strip().lower()
    return {"sample_id": sid, "accession": accession, "library_layout": layout}


def parse_metadata(path: Path) -> tuple[list[dict], list[str], str | None]:
    errors: list[str] = []
    rows: list[dict] = []
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        if not fieldnames:
            return [], [f"metadata: empty file (no header) at {path}"], None
        fmt = detect_metadata_format(fieldnames)
        if fmt is None:
            errors.append(
                f"metadata header is not recognized. Expected one of:\n"
                f"  Standard:         {sorted(STANDARD_REQUIRED_COLS)}\n"
                f"  SRA Run Selector: {sorted(SRA_RUN_SELECTOR_REQUIRED_COLS)} "
                f"(plus 'Sample Name'/'SampleName'/'BioSample' for sample IDs)\n"
                f"Found: {fieldnames}\n"
                f"Either rename your columns to the Standard format, or use a "
                f"Run Selector download from {RUNSELECTOR_URL} (or raw eutils "
                f"efetch runinfo output)."
            )
            return [], errors, fmt

        seen_accessions: dict[str, int] = {}
        seen_pairs: dict[tuple[str, str], int] = {}
        for row_idx, raw in enumerate(reader, start=2):
            row = (
                normalize_sra_run_selector_row(raw)
                if fmt == "sra_run_selector"
                else {k: (raw.get(k) or "").strip() for k in ("sample_id", "accession", "library_layout")}
            )
            sid = row["sample_id"]
            acc = row["accession"]
            layout = row["library_layout"]

            if not sid:
                errors.append(f"metadata row {row_idx}: sample_id is empty")
            elif not SAMPLE_ID_PATTERN.match(sid):
                errors.append(
                    f"metadata row {row_idx}: sample_id '{sid}' contains whitespace "
                    f"(samplesheet requires no spaces)"
                )

            if not acc:
                errors.append(f"metadata row {row_idx}: accession is empty for sample_id '{sid}'")
            elif not RUN_PATTERN.match(acc):
                errors.append(
                    f"metadata row {row_idx}: accession '{acc}' is not a run accession "
                    f"(expected SRR/ERR/DRR). Non-run accessions (PRJNA, SRX, SRP, "
                    f"GSE, GSM) must be expanded to runs before this step — see "
                    f"SKILL.md 'Expansion via E-utilities'."
                )
            elif acc in seen_accessions:
                errors.append(
                    f"metadata row {row_idx}: duplicate accession '{acc}' "
                    f"(first seen row {seen_accessions[acc]}). Each run accession must appear at most once."
                )
            else:
                seen_accessions[acc] = row_idx

            pair_key = (sid, acc)
            if pair_key in seen_pairs:
                errors.append(
                    f"metadata row {row_idx}: duplicate (sample_id, accession) pair "
                    f"('{sid}', '{acc}') (first seen row {seen_pairs[pair_key]})"
                )
            else:
                seen_pairs[pair_key] = row_idx

            if layout not in LIBRARY_LAYOUTS:
                errors.append(
                    f"metadata row {row_idx}: library_layout '{layout}' is not in "
                    f"{sorted(LIBRARY_LAYOUTS)}"
                )

            rows.append({
                "sample_id": sid, "accession": acc, "library_layout": layout, "_row": row_idx,
            })
    return rows, errors, fmt


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--metadata-csv",
        required=True,
        type=Path,
        help=(
            "Required. Run-level metadata CSV in either Standard format "
            "(sample_id, accession, library_layout) or SRA Run Selector / "
            "eutils-runinfo format (Run, LibraryLayout, …). The CSV must be "
            "run-level; non-run accessions must be expanded by Claude (via "
            f"eutils WebFetch — see SKILL.md) or downloaded from {RUNSELECTOR_URL}."
        ),
    )
    p.add_argument("--output", type=Path,
                   help="Optional: write expanded_metadata.csv to this path")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    if not args.metadata_csv.exists():
        print(
            f"ERROR: --metadata-csv path not found: {args.metadata_csv}\n"
            f"Hint: have Claude expand the user's accession via eutils (see "
            f"SKILL.md 'Expansion via E-utilities'), or download manually from\n"
            f"  {RUNSELECTOR_URL}?acc=<your-accession>\n"
            f"(click 'Total' → 'Metadata' → 'Download').",
            file=sys.stderr,
        )
        return 1

    rows, metadata_errors, fmt = parse_metadata(args.metadata_csv)
    if metadata_errors:
        print(f"Metadata CSV failed with {len(metadata_errors)} error(s):", file=sys.stderr)
        for e in metadata_errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    if args.verbose:
        print(
            f"Validation OK ({len(rows)} metadata row(s) in {fmt!r} format).",
            file=sys.stderr,
        )

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["sample_id", "run_accession", "library_layout"],
            )
            writer.writeheader()
            for row in rows:
                writer.writerow({
                    "sample_id":      row["sample_id"],
                    "run_accession":  row["accession"],
                    "library_layout": row["library_layout"],
                })
        print(f"Wrote expanded metadata: {args.output} ({len(rows)} (sample, run) pair(s))")

    n_runs = len({r["accession"] for r in rows})
    n_samples = len({r["sample_id"] for r in rows})
    print(
        f"Validation OK: {n_samples} sample(s), {n_runs} run(s). "
        f"Source: {args.metadata_csv}, format: {fmt}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
