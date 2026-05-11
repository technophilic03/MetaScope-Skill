#!/usr/bin/env python3
"""
Render the Rutgers Amarel SLURM submission script for nf-core/metascopeprolifer.

Emits one file into --output-dir:

  * submit_metascope.sh - SLURM array job. Per array task: fetches one run's
                          FASTQ via fastq-dump. The last task to finish (via
                          per-task marker files + flock mutex) runs Nextflow
                          once over the full samplesheet.

The user submits with one sbatch (HPC submissions go through the scheduler,
never via plain `bash`):

    sbatch submit_metascope.sh

Site values come from EITHER:
  - --slurm-config <yaml>: the SLURM_directives.yaml cache the skill writes
    (and re-uses) at ./metascope-microbiome/SLURM_directives.yaml
  - Individual CLI flags (--account, --partition, ...) which override
    matching values in the YAML for ad-hoc runs.

Database paths come from --db-config <yaml> (the databases.yaml cache) keyed
by --database <name>. Individual --db-* flags override registry values for
custom/one-off databases.

Refuses to render if:
  - any required value is still missing after merging YAML + CLI overrides,
  - any <PLACEHOLDER> marker remains in the merged values,
  - any {{ }} or {% %} token remains in any rendered output.

Output: ready-to-submit script. Does NOT submit - the user runs `sbatch`.

References: references/metascope-nextflow.md, SKILL.md Step 2 (SLURM directives).
"""
from __future__ import annotations  # makes type hints lazy: 3.7+ compatible

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


PLACEHOLDER_RE = re.compile(r"<[A-Za-z][A-Za-z0-9_:.\- ]*>")
JINJA_LEFTOVER_RE = re.compile(r"{{[^}]*}}|{%[^%]*%}")

TEMPLATE_NAME = "slurm_array.sh.j2"
OUTPUT_NAME = "submit_metascope.sh"

DEFAULT_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "assets"

# Path args we resolve to absolute on parse, so the rendered SLURM script's
# `cd "$WORK_DIR"` doesn't strand relative paths.
def abs_path(s: str) -> Path:
    return Path(s).resolve()


# SLURM directive fields. (yaml_key, cli_attr, label, required, default)
# Matches SKILL.md Step 2 table plus the fields the template needs.
SLURM_FIELDS = [
    # SKILL.md Step 2 table:
    ("partition",          "partition",          "SLURM --partition",         True,  None),
    ("job_name",           "job_name",           "Job name",                  False, "metascope-run"),
    ("default_time",       "time",               "Walltime (HH:MM:SS)",       True,  None),
    ("default_mem",        "mem",                "Memory (e.g. 200G)",        True,  None),
    ("default_cpus",       "cpus",               "CPUs per task",             True,  None),
    ("scratch_dir",        "scratch_dir",        "Scratch dir",               True,  None),
    ("work_dir",           "work_dir",           "Nextflow work dir",         True,  None),
    ("outdir",             "outdir",             "Pipeline outdir",           True,  None),
    ("log_dir",            "log_dir",            "SLURM log dir",             True,  None),
    # Pipeline plumbing - kept in the same YAML so one cache covers a full
    # run. Optional on the CLI; populated from YAML defaults or sensible
    # constants for users who only filled out the SKILL.md Step 2 fields.
    ("account",            "account",            "SLURM --account (optional)",False, None),
    ("module_loads",       "module_loads",       "module load lines",         True,  None),
    ("nextflow_profile",   "nextflow_profile",   "Nextflow profile",          True,  "singularity"),
    ("pipeline_ref",       "pipeline_ref",       "Pipeline ref",              True,  "nf-core/metascopeprolifer"),
    ("extra_pipeline_args","extra_pipeline_args","Extra `nextflow run` flags",False, ""),
]

# YAML-derived path fields we absolutize after merge. Same reason as abs_path:
# the rendered script `cd`s into WORK_DIR, after which relative paths break.
SLURM_PATH_FIELDS = ("scratch_dir", "work_dir", "outdir", "log_dir")

# Database fields. (yaml_key, cli_attr, required)
# `cli_attr` matches the SKILL.md Step 4 flag names verbatim.
DB_FIELDS = [
    ("metascope_index_dir",      "db_index_dir",      True),
    ("metascope_target",         "db_target",         True),
    ("metascope_filter",         "db_filter",         False),
    ("metascope_accession_path", "db_accession_path", True),
    ("metascope_db_path",        "db_blast_path",     True),
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
    """Resolve database paths from registry + CLI overrides. (resolved, errors)."""
    errors: list[str] = []
    databases = registry.get("databases", {})

    # Allow ad-hoc DBs: if `key` isn't in the registry but every required
    # path is supplied via CLI, accept it as a one-off custom database.
    if key not in databases:
        cli_supplied = {yk: cli_overrides.get(yk) for yk, _, _ in DB_FIELDS}
        required_keys = {yk for yk, _, req in DB_FIELDS if req}
        if all(cli_supplied.get(k) for k in required_keys):
            return cli_supplied, []
        return {}, [
            f"database key '{key}' not in registry. Available: {sorted(databases.keys())}. "
            f"For a one-off custom DB, supply every --db-* flag (index-dir, target, "
            f"accession-path, blast-path)."
        ]
    entry = databases[key]

    resolved: dict[str, str | None] = {}
    for yaml_key, _cli_attr, _required in DB_FIELDS:
        cli_val = cli_overrides.get(yaml_key)
        registry_val = entry.get(yaml_key)
        resolved[yaml_key] = cli_val if cli_val is not None else registry_val

    for yaml_key, _, required in DB_FIELDS:
        if required and not resolved.get(yaml_key):
            errors.append(
                f"database resolution: required field '{yaml_key}' is empty for entry "
                f"'{key}'. Supply via --{yaml_key.replace('_', '-')} or fill the registry."
            )

    return resolved, errors


def merge_slurm(yaml_defaults: dict, args: argparse.Namespace) -> tuple[dict, list[str]]:
    merged: dict[str, object] = {}
    for yaml_key, cli_attr, _label, _required, default in SLURM_FIELDS:
        cli_val = getattr(args, cli_attr, None)
        yaml_val = yaml_defaults.get(yaml_key)
        if cli_val is not None:
            merged[yaml_key] = cli_val
        elif yaml_val is not None:
            merged[yaml_key] = yaml_val
        else:
            merged[yaml_key] = default

    errors: list[str] = []
    for yaml_key, cli_attr, label, required, _default in SLURM_FIELDS:
        if required and merged.get(yaml_key) in (None, ""):
            errors.append(
                f"missing required SLURM value: {yaml_key} ({label}). "
                f"Provide via --{cli_attr.replace('_', '-')} or in --slurm-config."
            )
    return merged, errors


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)

    p.add_argument("--slurm-config", required=True, type=Path,
                   help="YAML cache of SLURM directives "
                        "(e.g. ./metascope-microbiome/SLURM_directives.yaml). "
                        "CLI flags below override matching values.")

    # SLURM directive overrides (all optional - YAML is the primary source).
    p.add_argument("--account",          help="SLURM --account (optional on Amarel)")
    p.add_argument("--partition",        help="SLURM --partition (Amarel: main, gpu, mem, ...)")
    p.add_argument("--time",             help="Walltime e.g. 12:00:00")
    p.add_argument("--mem",              help="Memory e.g. 200G")
    p.add_argument("--cpus",             help="CPUs per task e.g. 16")
    p.add_argument("--module-loads",     help="Multi-line module-load script")
    p.add_argument("--nextflow-profile", help="nf-core profile e.g. singularity")
    p.add_argument("--pipeline-ref",     help="Pipeline ref e.g. nf-core/metascopeprolifer")
    p.add_argument("--extra-pipeline-args", help="Extra args appended to `nextflow run`")
    p.add_argument("--scratch-dir",      help="Absolute path to scratch")
    p.add_argument("--work-dir",         help="Nextflow work directory")
    p.add_argument("--outdir",           help="Pipeline --outdir")
    p.add_argument("--log-dir",          help="SLURM stdout/stderr log dir")

    # Database
    p.add_argument("--db-config", type=Path, default=None,
                   help="Optional. YAML registry of databases "
                        "(e.g. ./metascope-microbiome/databases.yaml). Omit "
                        "for one-off runs where the user opted not to cache; "
                        "in that case supply every required --db-* flag below.")
    p.add_argument("--database", default="custom",
                   help="Key into the registry (e.g. silva_138). Defaults to "
                        "'custom' for ad-hoc runs without --db-config; appears "
                        "as a label in the rendered script's header comment.")
    p.add_argument("--db-index-dir",      help="Override metascope_index_dir")
    p.add_argument("--db-target",         help="Override metascope_target")
    p.add_argument("--db-filter",         help="Override metascope_filter (optional)")
    p.add_argument("--db-accession-path", help="Override metascope_accession_path")
    p.add_argument("--db-blast-path",     help="Override metascope_db_path (BLAST DB)")

    # Run inputs / outputs
    p.add_argument("--template-dir", type=Path, default=DEFAULT_TEMPLATE_DIR,
                   help=f"Directory containing {TEMPLATE_NAME} "
                        f"(default: {DEFAULT_TEMPLATE_DIR})")
    p.add_argument("--runs-list", required=True, type=abs_path)
    p.add_argument("--samplesheet", required=True, type=abs_path)
    p.add_argument("--fastq-dir", required=True, type=abs_path)
    p.add_argument("--merges-file", type=abs_path, default=None,
                   help="Path to merges.tsv produced by build_samplesheet.py "
                        "for samples with multiple runs. Defaults to "
                        "<samplesheet dir>/merges.tsv. The rendered script "
                        "checks for the file at runtime and skips merging if "
                        "it does not exist, so it is safe to leave the default "
                        "even when no merges are needed.")
    p.add_argument("--job-name", default=None,
                   help="Override job_name from YAML. Default 'metascope-run' "
                        "applies only when neither YAML nor CLI sets it.")
    p.add_argument("--output-dir", required=True, type=abs_path,
                   help=f"Directory to write {OUTPUT_NAME} into.")
    p.add_argument("--array-concurrency", type=int, default=None,
                   help="Optional cap on concurrent array tasks "
                        "(SLURM `%%N` syntax).")
    p.add_argument("--remove-fastq-after-run", action="store_true",
                   help="Append `rm -rf \"$FASTQ_DIR\"` to the rendered SLURM "
                        "script, gated on Nextflow success. Useful when "
                        "FASTQ_DIR is on scratch — Amarel does NOT auto-purge "
                        "scratch unless the user is over 1 TB quota AND files "
                        "are 90+ days idle, so cleanup is the user's "
                        "responsibility otherwise.")

    args = p.parse_args()

    # 1. Load SLURM YAML
    yaml_defaults = load_yaml(args.slurm_config)
    ph_issues = find_unfilled_placeholders(yaml_defaults)
    if ph_issues:
        print(
            f"ERROR: --slurm-config {args.slurm_config} has {len(ph_issues)} "
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
    slurm, slurm_errors = merge_slurm(yaml_defaults, args)

    # Absolutize YAML-derived paths so `cd "$WORK_DIR"` in the rendered
    # SLURM script doesn't strand relative path references.
    for k in SLURM_PATH_FIELDS:
        v = slurm.get(k)
        if v:
            slurm[k] = str(Path(v).resolve())

    # 3. Resolve database
    db_cli_overrides = {yk: getattr(args, attr) for yk, attr, _ in DB_FIELDS}
    if args.db_config is None:
        registry = {}            # ad-hoc run; resolve_database falls through
                                 # to the all-CLI-overrides path.
    else:
        registry = load_yaml(args.db_config)
    db, db_errors = resolve_database(registry, args.database, db_cli_overrides)

    all_errors = slurm_errors + db_errors
    if all_errors:
        print(f"Configuration failed with {len(all_errors)} error(s):", file=sys.stderr)
        for e in all_errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    # 4. Read runs list
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

    # 5. Verify template exists
    if not args.template_dir.is_dir():
        print(f"ERROR: --template-dir not found or not a directory: {args.template_dir}",
              file=sys.stderr)
        return 1
    if not (args.template_dir / TEMPLATE_NAME).exists():
        print(f"ERROR: template missing: {args.template_dir / TEMPLATE_NAME}", file=sys.stderr)
        return 1

    # 6. Build the rendering context
    context = {
        "ACCOUNT":             slurm.get("account") or "",
        "PARTITION":           slurm["partition"],
        "TIME":                slurm["default_time"],
        "MEM":                 slurm["default_mem"],
        "CPUS":                slurm["default_cpus"],
        "JOB_NAME":            slurm["job_name"],
        "LOG_DIR":             slurm["log_dir"],
        "MODULE_LOADS":        slurm["module_loads"],
        "PIPELINE_REF":        slurm["pipeline_ref"],
        "NEXTFLOW_PROFILE":    slurm["nextflow_profile"],
        "WORK_DIR":            slurm["work_dir"],
        "OUTDIR":              slurm["outdir"],
        "EXTRA_PIPELINE_ARGS": slurm.get("extra_pipeline_args") or "",
        "RUNS_LIST":           str(args.runs_list),
        "FASTQ_DIR":           str(args.fastq_dir),
        "SAMPLESHEET":         str(args.samplesheet),
        "MERGES_FILE":         str(args.merges_file or (args.samplesheet.parent / "merges.tsv")),
        "DATABASE_KEY":        args.database,
        "DB_INDEX_DIR":        db["metascope_index_dir"],
        "DB_TARGET":           db["metascope_target"],
        "DB_FILTER":           db.get("metascope_filter") or "",
        "DB_ACCESSION_PATH":   db["metascope_accession_path"],
        "DB_BLAST_PATH":       db["metascope_db_path"],
        "GENERATED_AT":        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "N_RUNS":              n_runs,
        "N_RUNS_MINUS_ONE":    n_runs - 1,
        "ARRAY_CONCURRENCY":   args.array_concurrency or "",
        "REMOVE_FASTQ_AFTER_RUN": args.remove_fastq_after_run,
    }

    env = Environment(
        loader=FileSystemLoader(args.template_dir),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )

    # 7. Render
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rendered = env.get_template(TEMPLATE_NAME).render(**context)
    leftover = JINJA_LEFTOVER_RE.findall(rendered)
    placeholder_leftover = PLACEHOLDER_RE.findall(rendered)
    if leftover:
        print(f"ERROR: rendered {OUTPUT_NAME} still contains Jinja tokens: {leftover[:5]}",
              file=sys.stderr)
        return 1
    if placeholder_leftover:
        print(f"ERROR: rendered {OUTPUT_NAME} still contains <PLACEHOLDER> markers: "
              f"{placeholder_leftover[:5]}", file=sys.stderr)
        return 1
    out_path = args.output_dir / OUTPUT_NAME
    out_path.write_text(rendered)
    out_path.chmod(0o755)

    print(f"Wrote submission script: {out_path}")
    print(f"  Runs: {n_runs} (array tasks 0..{n_runs - 1})")
    print(f"  Pipeline: {slurm['pipeline_ref']}")
    print(f"  Database: {args.database}")
    print()
    print("Submit with sbatch (HPC: use the scheduler, never plain bash):")
    print(f"  sbatch {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
