#!/usr/bin/env python3
"""
Validate the user's accession list and (optionally) metadata CSV, expand
non-run accessions to runs via pysradb, and write expanded_metadata.csv.

Inputs are flexible:

  Accession list — exactly one of:
    --accessions-file <path>            File with one accession per line (comments OK).
    --accessions-inline "<csv|spaced>"  Comma/whitespace-separated string.

  Metadata CSV — three modes:
    (a) --metadata-csv <path>  Pass an explicit Standard or SRA Run Selector CSV.
    (b) --metadata-csv omitted Auto-fetch from NCBI via pysradb. The fetched
                               table is written to <output dir>/SraRunTable.csv
                               (Run Selector format) so the user can inspect
                               or edit it before downstream steps.
    (c) --metadata-csv <path> where the path matches a previously auto-fetched
                               (and possibly edited) SraRunTable.csv — same as (a).

  Format auto-detection on the metadata CSV:
    Standard          — sample_id, accession, library_layout, [sample_type, …]
    SRA Run Selector  — Run, LibraryLayout, Sample Name, … (downloaded from
                        https://www.ncbi.nlm.nih.gov/Traces/study/ or auto-fetched
                        by this script). Mapped: Run→accession, LibraryLayout→
                        library_layout (lowercased), Sample Name→sample_id
                        (BioSample/Run fallback; spaces normalized to underscores).

Accepts these accession types in either source:
  run        — SRR/ERR/DRR
  experiment — SRX/ERX/DRX
  sample     — SRS/ERS/DRS
  study      — SRP/ERP/DRP
  bioproject — PRJNA/PRJEB/PRJDB
  GEO        — GSE/GSM

Outputs (when --output is given):
  expanded_metadata.csv with columns: sample_id, run_accession, library_layout

Surfaces every error at once. Exit 0 on success, 1 on validation/expansion failure.

Schema: references/metadata-schema.md
"""
from __future__ import annotations  # makes type hints lazy → 3.7+ compatible

import argparse
import csv
import re
import sys
from pathlib import Path

PATTERNS = {
    "run":         re.compile(r"^[SED]RR\d+$"),
    "experiment":  re.compile(r"^[SED]RX\d+$"),
    "sample":      re.compile(r"^[SED]RS\d+$"),
    "study":       re.compile(r"^[SED]RP\d+$"),
    "geo_series":  re.compile(r"^GSE\d+$"),
    "geo_sample":  re.compile(r"^GSM\d+$"),
    "bioproject":  re.compile(r"^PRJ(NA|EB|DB)\d+$"),
}
SAMPLE_ID_PATTERN = re.compile(r"^\S+$")
LIBRARY_LAYOUTS = {"single", "paired"}

STANDARD_REQUIRED_COLS = {"sample_id", "accession", "library_layout"}
SRA_RUN_SELECTOR_REQUIRED_COLS = {"Run", "LibraryLayout"}


def categorize(acc: str) -> str | None:
    for kind, pat in PATTERNS.items():
        if pat.match(acc):
            return kind
    return None


def parse_accession_list_file(path: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    accs: list[str] = []
    seen: dict[str, int] = {}
    for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if categorize(line) is None:
            errors.append(
                f"accession_list line {lineno}: '{line}' is not a recognized SRA accession "
                f"(expected SRR/ERR/DRR run, SRX/ERX/DRX experiment, SRS/ERS/DRS sample, "
                f"SRP/ERP/DRP study, GSE/GSM, or PRJNA/PRJEB/PRJDB)"
            )
            continue
        if line in seen:
            errors.append(
                f"accession_list line {lineno}: duplicate '{line}' (first seen line {seen[line]})"
            )
            continue
        seen[line] = lineno
        accs.append(line)
    return accs, errors


def parse_inline_accessions(s: str) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    accs: list[str] = []
    seen: dict[str, int] = {}
    parts = re.split(r"[,;\s]+", (s or "").strip())
    pos = 0
    for part in parts:
        part = part.strip()
        if not part or part.startswith("#"):
            continue
        pos += 1
        if categorize(part) is None:
            errors.append(
                f"inline accession #{pos}: '{part}' is not a recognized SRA accession"
            )
            continue
        if part in seen:
            errors.append(
                f"inline accession #{pos}: duplicate '{part}' (first seen position {seen[part]})"
            )
            continue
        seen[part] = pos
        accs.append(part)
    return accs, errors


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
    sid_raw = (raw.get("Sample Name") or raw.get("BioSample") or raw.get("Run") or "").strip()
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
                f"(plus 'Sample Name' for sample IDs)\n"
                f"Found: {fieldnames}\n"
                f"Either rename your columns to the Standard format, or download "
                f"directly from SRA Run Selector and pass that file unchanged."
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
                    f"(Howard's samplesheet requires no spaces)"
                )

            if not acc:
                errors.append(f"metadata row {row_idx}: accession is empty for sample_id '{sid}'")
            elif categorize(acc) is None:
                errors.append(
                    f"metadata row {row_idx}: accession '{acc}' is not a recognized SRA accession type"
                )
            elif acc in seen_accessions:
                errors.append(
                    f"metadata row {row_idx}: duplicate accession '{acc}' "
                    f"(first seen row {seen_accessions[acc]}). Each accession must appear at most once."
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


def cross_check(accs: list[str], rows: list[dict]) -> list[str]:
    """Original (un-expanded) accessions in list and metadata must agree exactly.

    Skipped when metadata was auto-fetched from NCBI (the metadata is derived
    from the list, so they're aligned by construction).
    """
    errors: list[str] = []
    list_set = set(accs)
    metadata_set = {r["accession"] for r in rows if r["accession"]}
    for acc in sorted(list_set - metadata_set):
        errors.append(f"cross-check: '{acc}' is in accession list but missing from metadata")
    for acc in sorted(metadata_set - list_set):
        errors.append(f"cross-check: '{acc}' is in metadata but missing from accession list")
    return errors


def fetch_run_selector_metadata(
    accessions: list[str],
    output_path: Path,
    verbose: bool = False,
) -> list[str]:
    """Use pysradb to fetch metadata and write a CSV that matches a manual
    SRA Run Selector download (modulo the columns pysradb cannot provide,
    which are emitted as empty cells so the header still matches).

    Returns a list of error strings (empty on success).
    """
    try:
        from pysradb.sraweb import SRAweb  # type: ignore
    except ImportError:
        return [
            "pysradb is required for auto-fetch but is not installed. "
            "Run `bash scripts/setup.sh` (installs all skill deps) "
            "or supply --metadata-csv <path> manually."
        ]

    # Mapping lives in runselector_mapping.py so the test
    # (tests/test_pysradb_runselector_format.py) and this script share one
    # source of truth.
    from runselector_mapping import (
        RUNSELECTOR_COLUMNS,
        map_pysradb_row_to_runselector,
    )

    db = SRAweb()
    rows: list[dict] = []
    errors: list[str] = []
    for acc in accessions:
        if verbose:
            print(f"  fetching metadata for {acc}…", file=sys.stderr)
        try:
            df = db.sra_metadata(acc, detailed=True)
        except Exception as e:
            errors.append(f"fetch: pysradb raised on '{acc}': {e}")
            continue
        if df is None or len(df) == 0:
            errors.append(f"fetch: pysradb returned no metadata for '{acc}'")
            continue
        # pysradb returns the full experiment for a run accession. Filter to
        # the requested run so output mirrors Run Selector semantics
        # (one input run → one output row). Non-run accessions
        # (SRX/SRP/PRJNA/GSE/…) pass through.
        if categorize(acc) == "run" and "run_accession" in df.columns:
            filtered = df[df["run_accession"] == acc]
            if len(filtered) == 0:
                errors.append(
                    f"fetch: pysradb metadata for '{acc}' contained no row matching the run "
                    f"(rows returned: {df['run_accession'].tolist()})"
                )
                continue
            df = filtered
        for _, p_row in df.iterrows():
            rows.append(map_pysradb_row_to_runselector(p_row.to_dict()))
    if errors:
        return errors
    if not rows:
        return ["fetch: no metadata fetched for any accession"]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RUNSELECTOR_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    if verbose:
        print(f"  wrote auto-fetched metadata: {output_path} ({len(rows)} run(s))", file=sys.stderr)
    return []


def expand_to_runs(rows: list[dict], verbose: bool = False) -> tuple[list[dict], list[str]]:
    errors: list[str] = []
    expanded: list[dict] = []

    needs_pysradb = any(categorize(r["accession"]) != "run" for r in rows)
    db = None
    if needs_pysradb:
        try:
            from pysradb.sraweb import SRAweb  # type: ignore
        except ImportError:
            errors.append(
                "pysradb is required to expand non-run accessions but is not installed. "
                "Run `bash scripts/setup.sh` (installs all skill deps idempotently) "
                "or: pip install pysradb"
            )
            return [], errors
        db = SRAweb()

    for row in rows:
        kind = categorize(row["accession"])
        if kind == "run":
            expanded.append({
                "sample_id": row["sample_id"],
                "run_accession": row["accession"],
                "library_layout": row["library_layout"],
            })
            continue
        if verbose:
            print(
                f"  expanding {row['accession']} ({kind}) for sample {row['sample_id']}…",
                file=sys.stderr,
            )
        try:
            df = db.sra_metadata(row["accession"])
        except Exception as e:
            errors.append(
                f"expand: pysradb raised on '{row['accession']}' (sample '{row['sample_id']}'): {e}"
            )
            continue
        if df is None or len(df) == 0:
            errors.append(f"expand: pysradb returned no metadata for '{row['accession']}'")
            continue
        if "run_accession" not in df.columns:
            errors.append(
                f"expand: pysradb response for '{row['accession']}' lacks 'run_accession' column "
                f"(got: {list(df.columns)})"
            )
            continue
        runs = sorted(df["run_accession"].dropna().unique().tolist())
        if not runs:
            errors.append(f"expand: no runs found under '{row['accession']}' (sample '{row['sample_id']}')")
            continue
        if verbose:
            print(f"    → {len(runs)} run(s)", file=sys.stderr)
        for run in runs:
            expanded.append({
                "sample_id": row["sample_id"],
                "run_accession": run,
                "library_layout": row["library_layout"],
            })
    return expanded, errors


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--accessions-file", type=Path,
                   help="File with one accession per line (comments OK)")
    g.add_argument("--accessions-inline",
                   help="Comma/whitespace-separated accession list (alternative to --accessions-file)")
    p.add_argument("--metadata-csv", type=Path, default=None,
                   help="Optional. Metadata CSV (Standard or SRA Run Selector format). "
                        "If omitted, auto-fetched from NCBI via pysradb to "
                        "<output dir>/SraRunTable.csv (or ./SraRunTable.csv).")
    p.add_argument("--output", type=Path,
                   help="Optional: write expanded_metadata.csv to this path")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    # 1. Parse accession list
    if args.accessions_file is not None:
        if not args.accessions_file.exists():
            print(f"ERROR: accession list not found: {args.accessions_file}", file=sys.stderr)
            return 1
        accs, list_errors = parse_accession_list_file(args.accessions_file)
        list_source = f"file {args.accessions_file}"
    else:
        accs, list_errors = parse_inline_accessions(args.accessions_inline)
        list_source = "inline"

    if list_errors:
        print(f"Accession list failed with {len(list_errors)} error(s):", file=sys.stderr)
        for e in list_errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    # 2. Resolve metadata CSV (user-supplied OR auto-fetched)
    auto_fetched = False
    metadata_path = args.metadata_csv
    if metadata_path is None:
        if args.output:
            metadata_path = args.output.parent / "SraRunTable.csv"
        else:
            metadata_path = Path.cwd() / "SraRunTable.csv"
        print(
            f"--metadata-csv not given. Auto-fetching from NCBI for {len(accs)} accession(s) "
            f"→ {metadata_path}",
            file=sys.stderr,
        )
        fetch_errors = fetch_run_selector_metadata(accs, metadata_path, verbose=args.verbose)
        if fetch_errors:
            print(f"Auto-fetch failed with {len(fetch_errors)} error(s):", file=sys.stderr)
            for e in fetch_errors:
                print(f"  - {e}", file=sys.stderr)
            return 1
        auto_fetched = True
        print(f"Auto-fetched metadata: {metadata_path}", file=sys.stderr)
    else:
        if not metadata_path.exists():
            print(f"ERROR: --metadata-csv path not found: {metadata_path}", file=sys.stderr)
            return 1

    # 3. Parse metadata CSV
    rows, metadata_errors, fmt = parse_metadata(metadata_path)
    if metadata_errors:
        print(f"Metadata CSV failed with {len(metadata_errors)} error(s):", file=sys.stderr)
        for e in metadata_errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    # 4. Cross-check user-supplied accessions ↔ metadata.
    #    Auto-fetched metadata is by construction aligned with the list, so skip.
    cross_errors: list[str] = []
    if not auto_fetched:
        cross_errors = cross_check(accs, rows)
        if cross_errors:
            print(f"Cross-check failed with {len(cross_errors)} error(s):", file=sys.stderr)
            for e in cross_errors:
                print(f"  - {e}", file=sys.stderr)
            return 1

    # 5. Expand non-run accessions in metadata to runs (no-op when metadata is run-level)
    if args.verbose:
        print(
            f"Validation OK ({len(accs)} accessions from {list_source}, "
            f"{len(rows)} metadata rows in {fmt!r} format"
            f"{'; auto-fetched' if auto_fetched else ''}). Expanding…",
            file=sys.stderr,
        )
    expanded, expand_errors = expand_to_runs(rows, verbose=args.verbose)
    if expand_errors:
        print(f"Expansion failed with {len(expand_errors)} error(s):", file=sys.stderr)
        for e in expand_errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    # 6. Write expanded metadata
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["sample_id", "run_accession", "library_layout"],
            )
            writer.writeheader()
            for row in expanded:
                writer.writerow(row)
        print(f"Wrote expanded metadata: {args.output} ({len(expanded)} (sample, run) pair(s))")

    n_runs = len({r["run_accession"] for r in expanded})
    n_samples = len({r["sample_id"] for r in expanded})
    print(
        f"Validation + expansion OK: {n_samples} sample(s), {n_runs} run(s). "
        f"Source: {list_source}, format: {fmt}"
        f"{', auto-fetched' if auto_fetched else ''}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
