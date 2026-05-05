#!/usr/bin/env python3
"""
Read expanded_metadata.csv (post-validation/expansion) and write:
  - samplesheet.csv (sample, fastq_1, fastq_2) — possibly multi-row per sample
    when one input accession (e.g. SRX) expanded to multiple runs. nf-core
    pipelines treat same-sample multi-row as multiple lanes and merge per sample.
  - runs.txt — one unique run accession per line, used by the SLURM job's
    fastq-dump loop.

Predicts FASTQ paths from `fastq-dump --split-files --gzip` naming:
  paired:  <fastq_dir>/<RUN>_1.fastq.gz, <fastq_dir>/<RUN>_2.fastq.gz
  single:  <fastq_dir>/<RUN>.fastq.gz

Schema: references/metascope-nextflow.md
"""
from __future__ import annotations  # makes type hints lazy → 3.7+ compatible

import argparse
import csv
import sys
from pathlib import Path


# Resolve to absolute on parse, so paths baked into samplesheet.csv survive
# the SLURM script's `cd "$WORK_DIR"` step at runtime.
def abs_path(s: str) -> Path:
    return Path(s).resolve()


def predict_fastq_paths(run: str, layout: str, fastq_dir: Path) -> tuple[str, str]:
    if layout == "paired":
        return (
            str(fastq_dir / f"{run}_1.fastq.gz"),
            str(fastq_dir / f"{run}_2.fastq.gz"),
        )
    if layout == "single":
        return (str(fastq_dir / f"{run}.fastq.gz"), "")
    raise ValueError(f"unknown library_layout '{layout}' for run '{run}'")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--expanded-metadata", required=True, type=abs_path,
                   help="Path to expanded_metadata.csv (output of validate_inputs.py --output)")
    p.add_argument("--fastq-dir", required=True, type=abs_path,
                   help="Directory where fastq-dump will place downloaded FASTQs")
    p.add_argument("--samplesheet", required=True, type=abs_path,
                   help="Output: nf-core samplesheet (sample,fastq_1,fastq_2)")
    p.add_argument("--runs", required=True, type=abs_path,
                   help="Output: unique run accessions, one per line")
    args = p.parse_args()

    if not args.expanded_metadata.exists():
        print(f"ERROR: expanded metadata not found: {args.expanded_metadata}", file=sys.stderr)
        return 1

    args.samplesheet.parent.mkdir(parents=True, exist_ok=True)
    args.runs.parent.mkdir(parents=True, exist_ok=True)

    seen_runs: set[str] = set()
    n_rows = 0

    with args.expanded_metadata.open(newline="") as fin, \
         args.samplesheet.open("w", newline="") as fout_samplesheet, \
         args.runs.open("w") as fout_runs:
        reader = csv.DictReader(fin)
        writer = csv.writer(fout_samplesheet)
        writer.writerow(["sample", "fastq_1", "fastq_2"])

        for row in reader:
            sid = (row.get("sample_id") or "").strip()
            run = (row.get("run_accession") or "").strip()
            layout = (row.get("library_layout") or "").strip()
            if not (sid and run and layout):
                print(
                    f"ERROR: incomplete row in expanded_metadata: {row}. "
                    f"Run validate_inputs.py --output first.",
                    file=sys.stderr,
                )
                return 1
            try:
                fastq_1, fastq_2 = predict_fastq_paths(run, layout, args.fastq_dir)
            except ValueError as e:
                print(f"ERROR: {e}", file=sys.stderr)
                return 1
            writer.writerow([sid, fastq_1, fastq_2])
            n_rows += 1
            if run not in seen_runs:
                seen_runs.add(run)
                fout_runs.write(f"{run}\n")

    print(f"Wrote samplesheet: {args.samplesheet} ({n_rows} row(s), {len(seen_runs)} unique run(s))")
    print(f"Wrote runs list:   {args.runs}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
