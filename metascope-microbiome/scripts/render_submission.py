#!/usr/bin/env python3
"""
Render the Rutgers Amarel SLURM submission scripts for nf-core/metascopeprolifer.

Always emits two files into --output-dir:

  * submit_fetch.sh — SLURM array job, one task per run, runs `fastq-dump`.
  * submit_run.sh   — single Nextflow job, runs `nextflow run <pipeline_ref> ...`.
                      Submitted by the user with `--dependency=afterok:<fetch JobID>`
                      so it starts only after every fetch task succeeds.

The user submits both via sbatch (HPC submissions go through the scheduler,
never via plain `bash`):

    sbatch submit_fetch.sh
    # note the JobID printed (e.g. "Submitted batch job 12345"), then:
    sbatch --dependency=afterok:12345 submit_run.sh

Even for a single run, the pattern is one fetch task + one dependent run job —
this keeps a single workflow regardless of N. (The Rutgers Amarel guide
recommends array jobs for many tasks; we apply it uniformly so behaviour is
predictable.)

Site values come from EITHER:
  - Individual CLI flags (--account, --partition, ...) — primary path; used
    when the skill collects values interactively from the user, OR
  - --rutgers-config <yaml> — optional cache of the same values, written
    after the first interactive session for reuse.

If both are supplied, **CLI flags override the YAML**.

Refuses to render if:
  - the chosen database has status='wip' AND no --db-* overrides are given,
  - any required value is still missing after merging both sources,
  - any <PLACEHOLDER> marker remains in the merged values,
  - any {{ }} token remains in any rendered output.

Output: ready-to-submit scripts. Does NOT submit — the user runs `sbatch`.

Reference: references/howard-nextflow.md, references/rutgers-hpc.md, references/databases.yaml
"""
from __future__ import annotations  # makes type hints lazy → 3.7+ compatible

import argparse
import re
import sys
from datetime import datetime, timezone
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

try:
    from jinja2 import Environment, FileSystemLoader, StrictUndefined
except ImportError:
    print(
        "ERROR: Jinja2 missing. Run `bash scripts/setup.sh` or: pip install jinja2",
        file=sys.stderr,
    )
    sys.exit(1)


PLACEHOLDER_RE = re.compile(r"<[A-Z_][A-Z0-9_-]*>|<your-[a-z0-9-]+>")
JINJA_LEFTOVER_RE = re.compile(r"{{[^}]*}}|{%[^%]*%}")

# Files emitted into --output-dir, in render order.
OUTPUTS = [
    # (template_filename,             output_filename)
    ("slurm_array_fetch.sh.j2",       "submit_fetch.sh"),
    ("slurm_array_run.sh.j2",         "submit_run.sh"),
]

# Default location of the bundled templates, relative to this script.
DEFAULT_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "assets"

# Rutgers fields the merged config must populate. (yaml_key, cli_attr, label, required)
RUTGERS_FIELDS = [
    ("account",            "account",           "SLURM --account",           False),
    ("partition",          "partition",         "SLURM --partition",         True),
    ("default_time",       "time",              "Walltime (e.g. 12:00:00)",  True),
    ("default_mem",        "mem",               "Memory (e.g. 64G)",         True),
    ("default_cpus",       "cpus",              "CPUs per task",             True),
    ("module_loads",       "module_loads",      "module load lines",         True),
    ("nextflow_profile",   "nextflow_profile",  "Nextflow profile",          True),
    ("pipeline_ref",       "pipeline_ref",      "Pipeline ref",              True),
    ("extra_pipeline_args","extra_pipeline_args","Extra `nextflow run` flags",False),
    ("scratch_dir",        "scratch_dir",       "Scratch dir",               True),
    ("work_dir",           "work_dir",          "Nextflow work dir",         True),
    ("outdir",             "outdir",            "Pipeline outdir",           True),
    ("log_dir",            "log_dir",           "SLURM log dir",             True),
]

# Database fields. CLI overrides allow per-run custom DB without editing databases.yaml.
DB_FIELDS = [
    # (yaml_key,                 cli_attr,           required)
    ("metascope_index_dir",      "db_index_dir",     True),
    ("metascope_target",         "db_target",        True),
    ("metascope_filter",         "db_filter",        False),
    ("metascope_accession_path", "db_accession_path",True),
    ("metascope_db_path",        "db_db_path",       True),
]


def load_yaml(path: Path) -> dict:
    if not path.exists():
        print(f"ERROR: file not found: {path}", file=sys.stderr)
        sys.exit(1)
    with path.open() as f:
        return yaml.safe_load(f) or {}


def find_unfilled_placeholders(d: dict, prefix: str = "") -> list[str]:
    issues = []
    for k, v in d.items():
        path = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            issues.extend(find_unfilled_placeholders(v, path))
        elif isinstance(v, str) and PLACEHOLDER_RE.search(v):
            issues.append(f"{path}: '{v}'")
    return issues


def resolve_database(registry: dict, key: str, cli_overrides: dict) -> tuple[dict, list[str]]:
    """Resolve database paths from registry + CLI overrides. Returns (resolved_paths, errors)."""
    errors: list[str] = []
    databases = registry.get("databases", {})
    if key not in databases:
        return {}, [
            f"database key '{key}' not in registry. Available: {sorted(databases.keys())}. "
            f"See references/databases.yaml, or supply --db-* flags for an ad-hoc DB."
        ]
    entry = databases[key]
    status = entry.get("status", "wip")

    resolved: dict[str, str | None] = {}
    for yaml_key, _cli_attr, _required in DB_FIELDS:
        cli_val = cli_overrides.get(yaml_key)
        registry_val = entry.get(yaml_key)
        resolved[yaml_key] = cli_val if cli_val is not None else registry_val

    if status == "wip":
        cli_supplied = {k for k, v in cli_overrides.items() if v}
        required_keys = {yk for yk, _, req in DB_FIELDS if req}
        if not required_keys.issubset(cli_supplied):
            notes = entry.get("notes", "").strip()
            msg = (
                f"database '{key}' has status='wip' — paths are not yet filled in the registry. "
                f"Either edit references/databases.yaml and set status: available, or supply "
                f"every required path on the CLI with --db-index-dir, --db-target, "
                f"--db-accession-path, --db-db-path."
            )
            if notes:
                msg += f" Notes: {notes}"
            errors.append(msg)
            return resolved, errors

    if status not in ("available", "wip", "pass-through"):
        errors.append(
            f"database '{key}' has unknown status '{status}'. "
            f"Expected 'available', 'wip', or 'pass-through'."
        )

    for yaml_key, _, required in DB_FIELDS:
        if required and not resolved.get(yaml_key):
            errors.append(
                f"database resolution: required field '{yaml_key}' is empty. "
                f"Supply via --{yaml_key.replace('_', '-')} or fill the registry entry."
            )

    return resolved, errors


def merge_rutgers(yaml_defaults: dict, args: argparse.Namespace) -> tuple[dict, list[str]]:
    merged: dict[str, object] = {}
    for yaml_key, cli_attr, _label, _required in RUTGERS_FIELDS:
        cli_val = getattr(args, cli_attr, None)
        yaml_val = yaml_defaults.get(yaml_key)
        merged[yaml_key] = cli_val if cli_val is not None else yaml_val

    errors: list[str] = []
    for yaml_key, cli_attr, label, required in RUTGERS_FIELDS:
        if required and not merged.get(yaml_key):
            errors.append(
                f"missing required Rutgers value: {yaml_key} ({label}). "
                f"Provide via --{cli_attr.replace('_', '-')} or in --rutgers-config."
            )
    return merged, errors


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)

    p.add_argument("--rutgers-config", type=Path,
                   help="Optional YAML cache of site values. CLI flags below override these.")

    # Rutgers site values
    p.add_argument("--account",          help="SLURM --account (optional on Amarel; omit if your group doesn't use one)")
    p.add_argument("--partition",        help="SLURM --partition (Amarel: main, gpu, mem, nonpre, graphical)")
    p.add_argument("--time",             help="Walltime e.g. 12:00:00 or D-HH:MM:SS")
    p.add_argument("--mem",              help="Memory e.g. 64G")
    p.add_argument("--cpus",             help="CPUs per task e.g. 16")
    p.add_argument("--module-loads",     help="Multi-line module-load script (newlines allowed)")
    p.add_argument("--nextflow-profile", help="nf-core profile e.g. singularity,slurm")
    p.add_argument("--pipeline-ref",     help="Pipeline ref e.g. nf-core/metascopeprolifer")
    p.add_argument("--extra-pipeline-args", help="Extra args appended to `nextflow run`")
    p.add_argument("--scratch-dir",      help="Absolute path to scratch")
    p.add_argument("--work-dir",         help="Nextflow work directory")
    p.add_argument("--outdir",           help="Pipeline --outdir")
    p.add_argument("--log-dir",          help="SLURM stdout/stderr log dir")

    # Database
    p.add_argument("--database-registry", required=True, type=Path)
    p.add_argument("--database", required=True, help="Key into the registry (e.g. silva_138, custom)")
    p.add_argument("--db-index-dir",      help="Override metascope_index_dir for this run")
    p.add_argument("--db-target",         help="Override metascope_target")
    p.add_argument("--db-filter",         help="Override metascope_filter (optional)")
    p.add_argument("--db-accession-path", help="Override metascope_accession_path")
    p.add_argument("--db-db-path",        help="Override metascope_db_path")

    # Run inputs / outputs
    p.add_argument("--template-dir", type=Path, default=DEFAULT_TEMPLATE_DIR,
                   help=f"Directory containing slurm_array_*.sh.j2 templates "
                        f"(default: {DEFAULT_TEMPLATE_DIR})")
    p.add_argument("--runs-list", required=True, type=Path)
    p.add_argument("--samplesheet", required=True, type=Path)
    p.add_argument("--fastq-dir", required=True, type=Path)
    p.add_argument("--job-name", default="metascope-microbiome")
    p.add_argument("--output-dir", required=True, type=Path,
                   help="Directory to write submit_fetch.sh, submit_run.sh, chain.sh into.")
    p.add_argument("--array-concurrency", type=int, default=None,
                   help="Optional cap on concurrent array tasks (SLURM `%%N` syntax). "
                        "Useful to avoid swamping the queue.")

    args = p.parse_args()

    # 1. Load YAML defaults if provided
    yaml_defaults: dict = {}
    if args.rutgers_config is not None:
        yaml_defaults = load_yaml(args.rutgers_config)
        ph_issues = find_unfilled_placeholders(yaml_defaults)
        if ph_issues:
            print(
                f"ERROR: --rutgers-config {args.rutgers_config} has {len(ph_issues)} "
                f"unfilled <PLACEHOLDER>:",
                file=sys.stderr,
            )
            for i in ph_issues:
                print(f"  - {i}", file=sys.stderr)
            print(
                "Either fix the YAML or override the affected fields via CLI flags.",
                file=sys.stderr,
            )
            return 1

    # 2. Merge YAML defaults with CLI overrides
    rutgers, rutgers_errors = merge_rutgers(yaml_defaults, args)

    # 3. Resolve database
    db_cli_overrides = {yk: getattr(args, attr) for yk, attr, _ in DB_FIELDS}
    registry = load_yaml(args.database_registry)
    db, db_errors = resolve_database(registry, args.database, db_cli_overrides)

    all_errors = rutgers_errors + db_errors
    if all_errors:
        print(f"Configuration failed with {len(all_errors)} error(s):", file=sys.stderr)
        for e in all_errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    # 4. Count runs
    if not args.runs_list.exists():
        print(f"ERROR: --runs-list not found: {args.runs_list}", file=sys.stderr)
        return 1
    runs = [
        line.strip()
        for line in args.runs_list.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    n_runs = len(runs)
    if n_runs == 0:
        print(f"ERROR: --runs-list {args.runs_list} has no usable runs.", file=sys.stderr)
        return 1

    # 5. Verify templates exist
    if not args.template_dir.is_dir():
        print(f"ERROR: --template-dir not found or not a directory: {args.template_dir}",
              file=sys.stderr)
        return 1
    for tpl_name, _ in OUTPUTS:
        if not (args.template_dir / tpl_name).exists():
            print(f"ERROR: template missing: {args.template_dir / tpl_name}", file=sys.stderr)
            return 1

    # 6. Build the shared rendering context
    context = {
        "ACCOUNT":             rutgers.get("account") or "",
        "PARTITION":           rutgers["partition"],
        "TIME":                rutgers["default_time"],
        "MEM":                 rutgers["default_mem"],
        "CPUS":                rutgers["default_cpus"],
        "JOB_NAME":            args.job_name,
        "LOG_DIR":             rutgers["log_dir"],
        "MODULE_LOADS":        rutgers["module_loads"],
        "PIPELINE_REF":        rutgers["pipeline_ref"],
        "NEXTFLOW_PROFILE":    rutgers["nextflow_profile"],
        "WORK_DIR":            rutgers["work_dir"],
        "OUTDIR":              rutgers["outdir"],
        "EXTRA_PIPELINE_ARGS": rutgers.get("extra_pipeline_args") or "",
        "RUNS_LIST":           str(args.runs_list),
        "FASTQ_DIR":           str(args.fastq_dir),
        "SAMPLESHEET":         str(args.samplesheet),
        "DATABASE_KEY":        args.database,
        "DB_INDEX_DIR":        db["metascope_index_dir"],
        "DB_TARGET":           db["metascope_target"],
        "DB_FILTER":           db.get("metascope_filter") or "",
        "DB_ACCESSION_PATH":   db["metascope_accession_path"],
        "DB_DB_PATH":          db["metascope_db_path"],
        "GENERATED_AT":        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "N_RUNS":              n_runs,
        "N_RUNS_MINUS_ONE":    n_runs - 1,
        "ARRAY_CONCURRENCY":   args.array_concurrency or "",
    }

    env = Environment(
        loader=FileSystemLoader(args.template_dir),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )

    # 7. Render the three files
    args.output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for tpl_name, out_name in OUTPUTS:
        rendered = env.get_template(tpl_name).render(**context)
        leftover = JINJA_LEFTOVER_RE.findall(rendered)
        placeholder_leftover = PLACEHOLDER_RE.findall(rendered)
        if leftover:
            print(f"ERROR: rendered {out_name} still contains Jinja tokens: {leftover[:5]}",
                  file=sys.stderr)
            return 1
        if placeholder_leftover:
            print(f"ERROR: rendered {out_name} still contains <PLACEHOLDER> markers: "
                  f"{placeholder_leftover[:5]}", file=sys.stderr)
            return 1
        out_path = args.output_dir / out_name
        out_path.write_text(rendered)
        out_path.chmod(0o755)
        written.append(out_path)

    fetch_path, run_path = written
    print(f"Wrote submission scripts to {args.output_dir}/ (run count: {n_runs}):")
    print(f"  {fetch_path.name}  — SLURM array job ({n_runs} task(s))")
    print(f"  {run_path.name}    — Nextflow run; submit with --dependency=afterok on the fetch JobID")
    print()
    print("Submit with two sbatch commands (HPC: use the scheduler, never plain bash):")
    print(f"  sbatch {fetch_path}")
    print(f"  # note the printed JobID (e.g. 'Submitted batch job 12345'), then:")
    print(f"  sbatch --dependency=afterok:<JobID> {run_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
