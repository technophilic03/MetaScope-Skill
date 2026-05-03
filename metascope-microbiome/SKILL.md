---
name: metascope-slurm
description: Generates a SLURM script for Rutgers Amarel HPC that wraps the nf-core/metascopeprolifer MetaScope Nextflow pipeline for 16S classification or shotgun metagenomic taxonomic profiling, taking SRA accessions and a metadata table to build the samplesheet, fetch FASTQs, and run the workflow against a configurable reference database. Use this skill whenever the user mentions MetaScope or running a microbiome profiling pipeline.
---

# MetaScope SLURM

Generates a Rutgers Amarel SLURM script that wraps `nextflow run nf-core/metascopeprolifer` for SRA-accession-based number.

## When to use

Use when users:
- Ask to generate an Amarel submission script for MetaScope on SRA accession(s)
- Want to build a samplesheet + submission script for nf-core/metascopeprolifer
- Need to wrap MetaScope Nextflow pipeline in a SLURM job

## Inputs the user provides

Two pieces of information; each can be supplied flexibly.

### 1. Accession numbers

Any SRA or GEO accession. The validator script (`scripts/validate_inputs.py`) accepts all standard prefix families and expands non-run
accessions to runs.

Flags:
Choose a flag to accept accession number input based on the type user provided. 
- `--accessions-file <path>` — if an accession list file was provided.
- `--accessions-inline "SRR12345,SRR45678,..."` — If the user pasted accessions in chat.


### 2. Metadata CSV

Two options. Ask the user which they want:

- **Option A — auto-fetch (default).** No flag. The validator script fetches the metadata table to
  `<run_dir>/SraRunTable.csv` for the user to inspect.
- **Option B — user-provided CSV.** Pass `--metadata-csv <path>`.

## Workflow steps

These are the procedures to follow. Copy this checklist and tick items as you go:

```
- [ ] 0. Run `bash scripts/setup.sh`
- [ ] 1. Configure SLURM directives settings interactively
- [ ] 2. Collect accessions + choose metadata source
- [ ] 3. Validate + expand accessions
- [ ] 4. Resolve database
- [ ] 5. Build samplesheet + runs.txt
- [ ] 6. Render submit_metascope.sh
- [ ] 7. Preflight
- [ ] 8. Present the `sbatch` command
- [ ] 9. Post-submission guidance
```

### Step 0: Setup (idempotent)
For setup for the first-time use, use the convenience script `scripts/setup.sh`:
```
bash scripts/setup.sh
```
The script installs python deps if any are missing, handles HPC `module load` detection, and creates a dedicated venv. Safe to run every session — it skips packages that are already importable.

After `setup.sh`, subsequent skill scripts can be invoked by activating the venv (`source ./metascope-microbiome/venv/bin/activate`).

### Step 1: Configure SLURM directives settings (ask the user)

Walk through these values one at a time. For each, suggest the default and/or hints but accept whatever the user provides.

If a saved cache exists at `./metascope-microbiome/SLURM_directives.yaml`, offer it as defaults. If the user accepts, only ask about values they want to change.

| Field          | Hint / example                                                          | CLI flag         |
|----------------|-------------------------------------------------------------------------|------------------|
| Partition      | Default: `main`          | `--partition`    |
| Job name       | Descriptive identifier, e.g., `metascope-run01`.                        | `--job-name`     |
| Time           | `HH:MM:SS`, e.g., `8:00:00`.                                           | `--time`         |
| CPUs per task  | e.g., `16`                       | `--cpus`         |
| Memory         | e.g., `64G` or `200G`                    | `--mem`          |
| Scratch dir         | e.g., `/scratch/<netid>`.            | `--scratch-dir`         |
| Work dir            | Nextflow work dir.                  | `--work-dir`            |
| Outdir              | Where pipeline results land. Default: `.`           | `--outdir`              |
| Log dir             | SLURM stdout/stderr. Default: `./logs`.                | `--log-dir`             |

When the user is happy, offer to save the answers to `./metascope-microbiome/SLURM_directives.yaml`:


### Step 2: Collect inputs
Ask the user if any input had not been provided:
1. **Accessions** — file path, or paste them inline.
2. **Metadata source** — auto-fetch or file path.

Halt until both are answered.

### Step 3: Validate + expand
To validate and/or expand the user's inputs, use the script `scripts/validate_inputs.py`.
```
python3 scripts/validate_inputs.py \
  (--accessions-file <path> | --accessions-inline "SRR123,SRR456,...") \
  [--metadata-csv <path>] \
  --output <run_dir>/expanded_metadata.csv
```
One of `--accessions-file` or `--accessions-inline` is required. Pass `--metadata-csv` to use user-supplied annotations; omit to auto-fetch from NCBI.

### Step 4: Resolve database (interactive)

MetaScope needs five reference paths: 
- `metascope_index_dir` - bowtie index directory
- `metascope_target` - target name
- `metascope_filter` - optional host filter
- `metascope_accession_path` - accessionTaxa
- `metascope_db_path` - BLAST db. 

Paths are user-supplied and optionally cached at `./metascope-microbiome/databases.yaml`.

1. **Check the cache.** If `./metascope-microbiome/databases.yaml` exists, list its entries with their `type`:
   ```
   # Example
   Cached databases:
     silva_138 (16S)
     my_shotgun_db (shotgun)
   ```
   If the file does not exist, skip this step.

2. **Ask which one.** "Pick a cached entry, or supply paths for a new database."

3. **Collect paths** (when picking new, or no cache exists). Walk through one at a time; accept whatever the user provides:

   | Prompt                                          | CLI flag                |
   |-------------------------------------------------|-------------------------|
   | Database name (used as the cache key)           | (cache only)            |
   | Bowtie2 index directory                         | `--db-index-dir`        |
   | Target reference index name                     | `--db-target`           |
   | Optional host filter index name (blank to skip) | `--db-filter`           |
   | Path to `accessionTaxa.sql`                     | `--db-accession-path`   |
   | Path to BLAST database                          | `--db-db-path`          |

4. **Show summary + confirm.** Echo back the resolved 5 paths before rendering.

5. **Offer to save** (only when paths were collected, not picked from cache). Append the entry to `./metascope-microbiome/databases.yaml`, creating the file if it doesn't exist:
   ```yaml
   databases:
     <name>:
       type: <16S|shotgun|other>
       metascope_index_dir: <…>
       metascope_target: <…>
       metascope_filter: <… or ~>
       metascope_accession_path: <…>
       metascope_db_path: <…>
   ```
   Future runs can just pick the name from the cache. Skip if the user says no.

Pass the resolved paths to `render_submission.py` via the `--db-*` flags.

### Step 5: Build samplesheet + runs list
To build the samplesheet for Nextflow input and run list for array rendering, use `scripts/build_samplesheet.py`:
```
python3 scripts/build_samplesheet.py \
  --expanded-metadata <run_dir>/expanded_metadata.csv \
  --fastq-dir <scratch_dir>/fastq \
  --samplesheet <run_dir>/samplesheet.csv \
  --runs <run_dir>/runs.txt
```
Predicts paths like `<scratch_dir>/fastq/<RUN>_1.fastq.gz` and writes both the nf-core samplesheet and a `runs.txt` (unique runs, one per line). Point the user to both files for sanity-checking.

### Step 6: Render submission script

Show the user a summary of values from Steps 1 and 4 and confirm. Then invoke `scripts/render_submission.py` to produce `<output-dir>/submit_metascope.sh` — the SLURM array script the user will submit.

```
python3 scripts/render_submission.py \
  --slurm-config ./metascope-microbiome/SLURM_directives.yaml \
  --db-config ./metascope-microbiome/databases.yaml --database <key> \
  --runs-list <run_dir>/runs.txt \
  --samplesheet <run_dir>/samplesheet.csv \
  --fastq-dir <scratch_dir>/fastq \
  --output-dir <run_dir>
```

### Step 7: Preflight
Conduct sanity check before directing the user to submit the job with `scripts/preflight.py`:

```
python3 scripts/preflight.py \
  --output-dir <run_dir> \
  --samplesheet <run_dir>/samplesheet.csv \
  --rutgers-config ./metascope-microbiome/SLURM_directives.yaml   # optional
```
Checks: 
- `submit_metascope.sh` exists + is executable 
- has #SBATCH directives (including `--array`)
- no leftover Jinja or `<PLACEHOLDER>` markers
- samplesheet header matches Nextflow pipelilne expected schema
- cached SLURM_directives.yaml (if given) has no placeholder leftovers.

### Step 8: Present the sbatch command (do NOT submit)

Show the user:
- The script `<run_dir>/submit_metascope.sh`, with a one-line summary.
- The single command to run:
  ```
  sbatch <run_dir>/submit_metascope.sh
  ```
- Where outputs and log files will land.

Submitting is the user's call — never run `sbatch` on their behalf. Ask user to return after submitting and proceed to step 9.

### Step 9: Post-submission guidance

After they run `sbatch`, tell them:
- Monitor with `squeue -u <netid>` — one array job with N tasks.
- Logs: `<log_dir>/slurm.<job-name>.<arrayid>_<task>.out` (per task).
- Pipeline outputs: under `<outdir>` per pipeline's conventions.
- If a single fetch task fails, re-submit just that index: `sbatch --array=<failed_index> submit_metascope.sh`.

## Outputs

The skill's deliverable is **one SLURM array script** plus the `sbatch` command. Specifically:

- `<run_dir>/submit_metascope.sh` — SLURM array (`#SBATCH --array=0-(N-1)`). Per-task, fetches one accession's FASTQ via `fastq-dump --split-files --gzip` (idempotent — skips runs already on disk), then runs the Nextflow pipeline over the full samplesheet. Pipeline ref is `nf-core/metascopeprolifer` (canonical) or `hjfan527/nf-core-metascopeprolifer` (fallback).
- `<run_dir>/samplesheet.csv` — nf-core-format samplesheet (`sample,fastq_1,fastq_2`).
- The literal `sbatch <run_dir>/submit_metascope.sh` command shown to the user.

After the user runs `sbatch`:
- Pipeline outputs land in `<outdir>` (from `SLURM_directives.yaml`).
- Logs in `<log_dir>` as `slurm.<job-name>.<arrayid>_<task>.out`.
- A MultiQC HTML report under `<outdir>/multiqc/` per nf-core convention.

## Error handling

Common failures and what to do:
- **Validator fails:** show every error at once (the script does this); ask the user to fix the metadata or SRR list and re-run step 3.
- **`<...>` placeholder remains in rendered output:** a cached YAML is incomplete. Tell the user which field; they edit and re-render.
- **fastq-dump fails inside the SLURM job:** see `references/sra-toolkit.md` "Known failure modes". Most often: no internet on compute nodes (run on login node first), or filesystem quota.
- **Nextflow can't find a database file:** a path collected in Step 4 is wrong or unreachable from compute nodes. Verify `metascope_index_dir`, `metascope_target`, etc. against the actual filesystem.

## References (when to read what)

| You need to know… | Read |
|--|--|
| Howard's pipeline params, samplesheet schema, version | `references/howard-nextflow.md` |
| fastq-dump invocation, module loading, common failures | `references/sra-toolkit.md` |
| Rutgers SLURM placeholders, what's generic vs. site-specific | `references/rutgers-hpc.md` |
| Metadata CSV format, validation rules, samplesheet mapping | `references/metadata-schema.md` |

## Notes for maintainers

- **Pipeline version pinning.** Default follows `main` of `hjfan527/nf-core-metascopeprolifer`. When the pipeline tags a release, switch users to the tag via the `--pipeline-ref` flag (or their cached `pipeline_ref`).
- **When Howard adds nf-core/fetchngs.** Drop the explicit `fastq-dump` step in `assets/slurm_template.sh.j2` and let the pipeline fetch via the samplesheet `run_accession` column. Update `references/sra-toolkit.md` to mark itself deprecated.
- **When the samplesheet schema changes.** Update `assets/samplesheet_template.csv`, `scripts/build_samplesheet.py`, and `references/howard-nextflow.md`.
