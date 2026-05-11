#!/usr/bin/env python3
"""
Read expanded_metadata.csv (post-validation/expansion) and write:
  - samplesheet.csv (sample, fastq_1, fastq_2) — exactly one row per
    sample_id (the pipeline's groupTuple() collapses by sample_id, then
    feeds the list straight into Trimmomatic which only accepts one input).
  - runs.txt — one unique run accession per line, used by the SLURM job's
    fastq-dump loop.
  - merges.tsv (--multi-run-mode merge only, and only if any sample has >1
    runs) — driver file for the SLURM merge step. TSV header
    `output<TAB>inputs` where `inputs` is a comma-separated list of absolute
    per-run FASTQ paths to concatenate into `output` (gzip is concat-safe).

Multi-run handling (samples with >1 runs in expanded_metadata.csv):
  --multi-run-mode merge (default; recommended for technical replicates):
      one samplesheet row per sample. Runs concatenated into
      <sample>.merged_{1,2}.fastq.gz by the rendered SLURM script before
      Nextflow launches. Output: one taxonomy profile per biosample.
  --multi-run-mode split:
      one samplesheet row per run. Duplicate sample_ids are disambiguated
      to <sample_id>_<run_accession>; singleton sample_ids are kept as-is.
      No merging happens; no merges.tsv is written. Output: one taxonomy
      profile per run (use when you want to keep technical replicates
      separate, e.g. for QC comparison).

The MetaScope pipeline groups input rows by sample_id via .groupTuple() but
does not insert a CAT_FASTQ step. Passing multi-row input as-is causes
Trimmomatic to receive both per-run FASTQs as positional args and crash with
"Unknown trimmer". Either pre-merging (merge) or per-run sample renaming
(split) avoids the crash; merge is the convention for technical replicates.

Predicts FASTQ paths from `fastq-dump --split-files --gzip` naming:
  paired (per run):     <fastq_dir>/<RUN>_1.fastq.gz, <fastq_dir>/<RUN>_2.fastq.gz
  single (per run):     <fastq_dir>/<RUN>_1.fastq.gz
  paired (merged):      <fastq_dir>/<SAMPLE>.merged_1.fastq.gz, <fastq_dir>/<SAMPLE>.merged_2.fastq.gz
  single (merged):      <fastq_dir>/<SAMPLE>.merged_1.fastq.gz
(`--split-files` always appends `_1` even for single-end runs; only the bare
`fastq-dump` command — which we don't use — produces `<RUN>.fastq.gz`.)

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


def per_run_paths(run: str, layout: str, fastq_dir: Path) -> tuple[str, str]:
    if layout == "paired":
        return (
            str(fastq_dir / f"{run}_1.fastq.gz"),
            str(fastq_dir / f"{run}_2.fastq.gz"),
        )
    if layout == "single":
        return (str(fastq_dir / f"{run}_1.fastq.gz"), "")
    raise ValueError(f"unknown library_layout '{layout}' for run '{run}'")


def merged_paths(sample: str, layout: str, fastq_dir: Path) -> tuple[str, str]:
    if layout == "paired":
        return (
            str(fastq_dir / f"{sample}.merged_1.fastq.gz"),
            str(fastq_dir / f"{sample}.merged_2.fastq.gz"),
        )
    if layout == "single":
        return (str(fastq_dir / f"{sample}.merged_1.fastq.gz"), "")
    raise ValueError(f"unknown library_layout '{layout}' for sample '{sample}'")


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
    p.add_argument("--merges", type=abs_path, default=None,
                   help="Output: TSV merge plan for samples with multiple runs. "
                        "Defaults to <samplesheet dir>/merges.tsv. Only written "
                        "when --multi-run-mode=merge AND at least one sample "
                        "has >1 runs.")
    p.add_argument("--multi-run-mode", choices=("merge", "split"), default="merge",
                   help="How to handle samples whose sample_id appears on >1 "
                        "rows of --expanded-metadata. 'merge' (default; "
                        "recommended for technical replicates): one samplesheet "
                        "row per sample, runs concatenated into a single FASTQ "
                        "by the rendered SLURM script. 'split': one row per "
                        "run; duplicate sample_ids disambiguated to "
                        "<sample>_<run>; no merging.")
    args = p.parse_args()

    if not args.expanded_metadata.exists():
        print(f"ERROR: expanded metadata not found: {args.expanded_metadata}", file=sys.stderr)
        return 1

    args.samplesheet.parent.mkdir(parents=True, exist_ok=True)
    args.runs.parent.mkdir(parents=True, exist_ok=True)
    merges_path = args.merges or (args.samplesheet.parent / "merges.tsv")

    # First pass: collect rows grouped by sample, preserving sample-first-seen order.
    # Multi-run samples need merging; per-sample layout must be consistent.
    samples_order: list[str] = []
    by_sample: dict[str, list[tuple[str, str]]] = {}   # sid -> [(run, layout), ...]
    seen_runs: list[str] = []
    seen_runs_set: set[str] = set()

    with args.expanded_metadata.open(newline="") as fin:
        reader = csv.DictReader(fin)
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
            if layout not in ("single", "paired"):
                print(f"ERROR: unknown library_layout '{layout}' for run '{run}'",
                      file=sys.stderr)
                return 1
            if sid not in by_sample:
                samples_order.append(sid)
                by_sample[sid] = []
            by_sample[sid].append((run, layout))
            if run not in seen_runs_set:
                seen_runs_set.add(run)
                seen_runs.append(run)

    # Per-sample layout consistency. In merge mode we'd be `cat`-ing SE+PE
    # together which is meaningless; in split mode each run keeps its own
    # row so layouts don't have to match — but mixing within one sample_id
    # is still a metadata bug worth flagging. Reject either way.
    layout_errors = []
    for sid in samples_order:
        layouts = {layout for _, layout in by_sample[sid]}
        if len(layouts) > 1:
            layout_errors.append(
                f"sample '{sid}' has mixed library_layout values {sorted(layouts)} "
                f"across its {len(by_sample[sid])} run(s); refusing to proceed."
            )
    if layout_errors:
        for e in layout_errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1

    # Second pass: write samplesheet, runs, and (in merge mode only) merges.
    merges: list[tuple[str, list[str]]] = []   # (output_path, [input_path, ...])
    n_singletons = 0
    n_multi = 0   # multi-run samples (merged or split-renamed depending on mode)

    with args.samplesheet.open("w", newline="") as fout_samplesheet:
        writer = csv.writer(fout_samplesheet)
        writer.writerow(["sample", "fastq_1", "fastq_2"])

        for sid in samples_order:
            runs = by_sample[sid]
            layout = runs[0][1]   # consistency already enforced above

            if len(runs) == 1:
                # Singleton: identical handling in both modes.
                run, _ = runs[0]
                fastq_1, fastq_2 = per_run_paths(run, layout, args.fastq_dir)
                writer.writerow([sid, fastq_1, fastq_2])
                n_singletons += 1
                continue

            n_multi += 1

            if args.multi_run_mode == "merge":
                merged_1, merged_2 = merged_paths(sid, layout, args.fastq_dir)
                writer.writerow([sid, merged_1, merged_2])
                # Record merge plan: one row per output FASTQ.
                inputs_1 = [per_run_paths(r, layout, args.fastq_dir)[0] for r, _ in runs]
                merges.append((merged_1, inputs_1))
                if layout == "paired":
                    inputs_2 = [per_run_paths(r, layout, args.fastq_dir)[1] for r, _ in runs]
                    merges.append((merged_2, inputs_2))
            else:   # split
                # Disambiguate by suffixing the run accession so each row has
                # a unique sample_id. No merging — each run becomes its own
                # taxonomy profile downstream.
                for run, _ in runs:
                    split_sid = f"{sid}_{run}"
                    fastq_1, fastq_2 = per_run_paths(run, layout, args.fastq_dir)
                    writer.writerow([split_sid, fastq_1, fastq_2])

    with args.runs.open("w") as fout_runs:
        for run in seen_runs:
            fout_runs.write(f"{run}\n")

    # Only write merges.tsv when there's something to merge.
    if merges:
        with merges_path.open("w", newline="") as fout_merges:
            mw = csv.writer(fout_merges, delimiter="\t")
            mw.writerow(["output", "inputs"])
            for output, inputs in merges:
                mw.writerow([output, ",".join(inputs)])

    print(
        f"Wrote samplesheet: {args.samplesheet} "
        f"({len(samples_order)} sample_id(s): {n_singletons} single-run, "
        f"{n_multi} multi-run; mode={args.multi_run_mode})"
    )
    print(f"Wrote runs list:   {args.runs} ({len(seen_runs)} unique run(s))")
    if merges:
        print(f"Wrote merge plan:  {merges_path} ({len(merges)} output FASTQ(s) to assemble)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
