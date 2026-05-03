#!/usr/bin/env python3
"""
Preflight checks before the user submits with `sbatch`.

Verifies the rendered submission scripts (submit_fetch.sh, submit_run.sh)
inside --output-dir:
  - each file exists, is executable, has a shebang and #SBATCH directives
  - no unreplaced Jinja tokens or <PLACEHOLDER> markers
  - the samplesheet parses and has the expected nf-core columns
  - --rutgers-config (if given) has no leftover placeholders

Returns exit 0 if all checks pass, 1 otherwise. Reports every failing check —
do not stop at the first error.
"""
from __future__ import annotations  # makes type hints lazy → 3.7+ compatible

import argparse
import csv
import os
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print(
        "ERROR: PyYAML missing. Run `bash scripts/setup.sh` (installs all skill deps) "
        "or: pip install pyyaml",
        file=sys.stderr,
    )
    sys.exit(1)


PLACEHOLDER_RE = re.compile(r"<[A-Z_][A-Z0-9_-]*>|<your-[a-z0-9-]+>")
JINJA_LEFTOVER_RE = re.compile(r"{{[^}]*}}|{%[^%]*%}")
EXPECTED_SAMPLESHEET_COLS = ["sample", "fastq_1", "fastq_2"]

EXPECTED_FILES = [
    "submit_fetch.sh",
    "submit_run.sh",
]


def check_script(path: Path) -> list[str]:
    errors = []
    if not path.exists():
        return [f"{path.name} not found: {path}"]
    if not os.access(path, os.X_OK):
        errors.append(f"{path.name} is not executable: {path} (chmod +x)")
    text = path.read_text()
    if not text.lstrip().startswith("#!"):
        errors.append(f"{path.name} missing shebang line ({path})")
    if "#SBATCH" not in text:
        errors.append(f"{path.name} contains no #SBATCH directives ({path})")
    leftover = JINJA_LEFTOVER_RE.findall(text)
    if leftover:
        errors.append(
            f"{path.name} has {len(leftover)} unreplaced Jinja token(s): {leftover[:3]} "
            f"(re-render with scripts/render_submission.py)"
        )
    placeholder_leftover = PLACEHOLDER_RE.findall(text)
    if placeholder_leftover:
        errors.append(
            f"{path.name} has {len(placeholder_leftover)} unreplaced <PLACEHOLDER> "
            f"marker(s): {placeholder_leftover[:3]}"
        )
    return errors


def check_samplesheet(path: Path) -> list[str]:
    errors = []
    if not path.exists():
        return [f"samplesheet not found: {path}"]
    with path.open(newline="") as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return [f"samplesheet is empty: {path}"]
        if header != EXPECTED_SAMPLESHEET_COLS:
            errors.append(
                f"samplesheet header is {header}, expected {EXPECTED_SAMPLESHEET_COLS} "
                f"(see references/howard-nextflow.md)"
            )
            return errors
        n_rows = sum(1 for _ in reader)
    if n_rows == 0:
        errors.append(f"samplesheet has header but no data rows: {path}")
    return errors


def check_rutgers_config(path: Path) -> list[str]:
    """Optional cache. Just check it parses and has no leftover placeholders;
    completeness is render_submission.py's job."""
    errors = []
    if not path.exists():
        return [f"--rutgers-config given but file not found: {path}"]
    with path.open() as f:
        try:
            cfg = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            return [f"--rutgers-config is not valid YAML: {path}: {e}"]
    for field, value in cfg.items():
        if isinstance(value, str) and PLACEHOLDER_RE.search(value):
            errors.append(
                f"rutgers config field '{field}' still has placeholder: '{value}' (in {path})"
            )
    return errors


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-dir", required=True, type=Path,
                   help="Directory containing submit_fetch.sh, submit_run.sh, chain.sh")
    p.add_argument("--samplesheet", required=True, type=Path)
    p.add_argument("--rutgers-config", type=Path,
                   help="Optional cached rutgers.yaml — only checked for placeholder leftovers")
    args = p.parse_args()

    all_errors: list[str] = []

    if not args.output_dir.is_dir():
        all_errors.append(f"--output-dir not a directory: {args.output_dir}")
    else:
        for fname in EXPECTED_FILES:
            all_errors += check_script(args.output_dir / fname)

    all_errors += check_samplesheet(args.samplesheet)

    if args.rutgers_config is not None:
        all_errors += check_rutgers_config(args.rutgers_config)

    if all_errors:
        print(f"Preflight failed with {len(all_errors)} issue(s):", file=sys.stderr)
        for e in all_errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    fetch = args.output_dir / "submit_fetch.sh"
    run = args.output_dir / "submit_run.sh"
    print("Preflight OK. Submit with:")
    print(f"  sbatch {fetch}")
    print(f"  # note the printed JobID, then:")
    print(f"  sbatch --dependency=afterok:<JobID> {run}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
