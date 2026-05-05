---
name: metascope-slurm
description: Generates a SLURM script for Rutgers Amarel HPC that wraps MetaScope Nextflow pipeline for 16S classification or shotgun metagenomic taxonomic profiling, taking SRA accessions and a metadata table to build the samplesheet, fetch FASTQs, and run the workflow against a configurable reference database. Use this skill when the user mentions MetaScope or running a microbiome profiling pipeline.
---

# MetaScope SLURM

Generates a Rutgers Amarel SLURM script that wraps MetaScope Nextflow pipeline.

## When to use

Use when users:
- Ask to generate an Amarel submission script for MetaScope on SRA accession(s)
- Want to build a samplesheet for nf-core/metascopeprolifer
- Need to wrap MetaScope Nextflow pipeline in a SLURM job

## Inputs the user provides

### 1. Accession(s)

Any SRA or GEO accession is acceptable as user input. Two levels of input:

- **Run-level**: consumed directly by the rest of the pipeline.
- **Non-run level**: may need expansion to runs first (e.g. BioProject, experiment, study, sample, GEO).

The Python scripts only consume run-level CSVs. When the user supplies a non-run accession, Claude does the expansion using `WebFetch` against NCBI E-utilities (see "Expansion via E-utilities" below).

### 2. Metadata CSV

The pipeline needs a CSV that has, at minimum, run accession + library layout per row. Two ways the CSV gets to disk:

- **User has one already** — they point at a Run Selector download.
- **User has only accessions** — Claude fetches a runinfo CSV via eutils (see “Expansion via E-utilities” below) and saves it as `<run_dir>/SraRunTable.csv`.

Either way, by the time Step 3 runs, there must be a single run-level CSV at a known path.

## Expansion via E-utilities

When the user supplies non-run accessions and no pre-built CSV, expand via `WebFetch` against NCBI E-utilities. Two endpoints, no auth:

**1. esearch** — translate accession to internal SRA UIDs:
```
https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=sra&term=<TERM>&retmax=100000
```
Response is XML with `<Id>` elements. Build `<TERM>` from the accession kind.

**2. efetch** — get a runinfo CSV for those UIDs:
```
https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=sra&id=<COMMA-SEPARATED-UIDS>&rettype=runinfo&retmode=text
```
Response is a CSV with appropriate columns.

**Save the efetch response unchanged to `<run_dir>/SraRunTable.csv`.**

**Politeness** — NCBI rate-limits anonymous traffic to ~3 req/s. Do not exceed rate-limits when having many accesisons to expand. 

**Manual fallback** — if `WebFetch` fails or the user prefers, point them at `https://www.ncbi.nlm.nih.gov/Traces/study/?acc=<their-accession>` to download the metadata table and get back to you.

## Workflow steps

These are the procedures to follow. Copy this checklist and tick items as you go:

```
- [ ] 0. Setup
- [ ] 1. Collect all inputs
- [ ] 2. Configure SLURM directives 
- [ ] 3. Validate the metadata CSV
- [ ] 4. Resolve database
- [ ] 5. Build samplesheet + runs.txt
- [ ] 6. Render submit_metascope.sh
- [ ] 7. Preflight
- [ ] 8. Present `sbatch` command + post-submission guidance
```

### Step 0: Setup
For setup for the first-time use, use the convenience script `scripts/setup.sh`:
```
bash scripts/setup.sh
```
The script installs python deps if any are missing, handles HPC `module load` detection, and creates a dedicated venv. Safe to run every session — it skips packages that are already importable.

After `setup.sh`, subsequent skill scripts can be invoked by activating the venv (`source ./metascope-microbiome/venv/bin/activate`).

### Step 1: Collect inputs
Ask the user for whichever of these isn't already in hand:
1. **Accessions** — pasted inline or a file path.
2. **Metadata CSV (optional)** — path to a Run Selector / runinfo CSV, if they already have one.
3. **FASTQ storage** — two questions, in order:
   - **Where to store the downloaded FASTQs?** Default: `<run_dir>/fastq` (kept alongside the rest of the run's outputs). Alternate: a path under the user's scratch (suggest `<scratch_dir>/<job_name>/fastq` ). Use this answer for `--fastq-dir` in Steps 5 and 6 — both scripts must receive the same value.
   - **Remove FASTQs after the pipeline finishes successfully?** Yes/no. If the user says yes, pass `--remove-fastq-after-run` to Step 6 — `rm -rf "$FASTQ_DIR"` will be appended to the rendered SLURM script, gated on Nextflow success.

Then, before proceeding:
- If **any accession isn't a run** (SRR/ERR/DRR) and no run-level CSV was provided, run the eutils expansion (see "Expansion via E-utilities" above) and save the runinfo CSV to `<run_dir>/SraRunTable.csv`.
- If **only runs were provided** and no metadata was given, you can either skip metadata fetching (you only need `LibraryLayout` for the samplesheet — ask the user) or fetch via efetch over the runs themselves to populate the CSV.

By the time Step 3 begins, there is one run-level CSV at a known path.

### Step 2: Configure SLURM directives (ask the user)

Walk through these values one at a time. Use the data from Step 1 to suggest informed hints. Accept whatever the user provides.

If a saved cache exists at `./metascope-microbiome/SLURM_directives.yaml`, offer it as defaults. If the user accepts, only ask about values they want to change.

| Field          | Hint / example                                                          | CLI flag         |
|----------------|-------------------------------------------------------------------------|------------------|
| Partition      | Default: `main`                                                         | `--partition`    |
| Job name       | Descriptive identifier, e.g., `metascope-run01`.                        | `--job-name`     |
| Time           | `HH:MM:SS`. Default:`12:00:00`. | `--time`         |
| CPUs per task  | Default: `16`.                  | `--cpus`         |
| Memory         | Default: `200G`.         | `--mem`          |
| Scratch dir    | e.g., `/scratch/<netid>`.                                               | `--scratch-dir`  |
| Work dir       | Nextflow work dir.                                                      | `--work-dir`     |
| Outdir         | Where pipeline results land. Default: `.`                               | `--outdir`       |
| Log dir        | SLURM stdout/stderr. Default: `./logs`.                                 | `--log-dir`      |

**Hardcoded `module_loads` (do NOT ask the user).** The array task needs these modules to find Python, fastq-dump, and Nextflow on Amarel. The template also extends `MODULEPATH` to include `/projects/community/modulefiles` before these run. Always pass this exact value via `--module-loads`:
```
module load python
module load sratoolkit
module load nextflow
module load java
module load singularity
```

When the user is happy, offer to save the answers to `./metascope-microbiome/SLURM_directives.yaml`.


### Step 3: Validate the metadata CSV
Use `scripts/validate_inputs.py` to verify the run-level CSV and write the table that downstream steps consume.
```
python3 scripts/validate_inputs.py \
  --metadata-csv <run_dir>/SraRunTable.csv \
  --output <run_dir>/expanded_metadata.csv
```

If the validator complains that an accession isn't a run, expansion was missed in Step 1 — go back, run eutils, then re-run.

Outputs:
- `<run_dir>/expanded_metadata.csv` — validated run-level metadata (`sample_id, run_accession, library_layout`).

**Multi-run-per-sample.** The validator allows the same `sample_id` on multiple rows when one biosample has multiple runs (`(sample_id, accession)` pairs must still be unique). Downstream, the samplesheet has matching `sample` values across rows with different fastq paths — the nf-core "multiple lanes per sample" pattern; the Nextflow pipeline merges these per sample.

### Step 4: Resolve database (interactive)

MetaScope needs up to five reference paths (filter is optional): 
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
   | Path to BLAST database                          | `--db-blast-path`          |

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

### Step 5: Build samplesheet + runs list
To build the samplesheet for Nextflow input and run list for array rendering, use `scripts/build_samplesheet.py`. Pass the same `--fastq-dir` the user picked in Step 1 (default `<run_dir>/fastq`):
```
python3 scripts/build_samplesheet.py \
  --expanded-metadata <run_dir>/expanded_metadata.csv \
  --fastq-dir <run_dir>/fastq \
  --samplesheet <run_dir>/samplesheet.csv \
  --runs <run_dir>/runs.txt
```
Predicts paths like `<run_dir>/fastq/<RUN>_1.fastq.gz` (or wherever the user put `--fastq-dir`) and writes both the nf-core samplesheet and a `runs.txt` (unique runs, one per line). Point the user to both files for sanity-checking.

### Step 6: Render submission script

Invoke `scripts/render_submission.py` to produce `<output-dir>/submit_metascope.sh` — the SLURM array script the user will submit. Pass the same `--fastq-dir` from Step 5; add `--remove-fastq-after-run` if the user opted into post-run cleanup in Step 1.

```
python3 scripts/render_submission.py \
  --slurm-config ./metascope-microbiome/SLURM_directives.yaml \
  --db-config ./metascope-microbiome/databases.yaml --database <key> \
  --runs-list <run_dir>/runs.txt \
  --samplesheet <run_dir>/samplesheet.csv \
  --fastq-dir <run_dir>/fastq \
  --output-dir <run_dir> \
  [--remove-fastq-after-run]
```

### Step 7: Preflight

Two checks, both before submission.

**(a) Static checks** on the rendered script and samplesheet:
```
python3 scripts/preflight.py \
  --output-dir <run_dir> \
  --samplesheet <run_dir>/samplesheet.csv \
  --slurm-config ./metascope-microbiome/SLURM_directives.yaml   # optional
```
Checks:
- `submit_metascope.sh` exists + is executable
- has `#SBATCH` directives (including `--array`)
- no leftover Jinja or `<PLACEHOLDER>` markers
- samplesheet header matches the pipeline's expected schema
- cached `SLURM_directives.yaml` (if given) has no placeholder leftovers

**(b) Pipeline dry-run** with `nextflow run -preview` — validates parameters, samplesheet schema, and input file readability against the actual pipeline without launching any compute:
```
nextflow run nf-core/metascopeprolifer -preview \
  -profile singularity \
  --input <run_dir>/samplesheet.csv \
  --outdir <outdir> \
  --metascope_index_dir <…> --metascope_target <…> \
  --metascope_accession_path <…> --metascope_db_path <…>
```
If `-preview` errors, fix the underlying issue and re-render before proceeding. See `references/metascope-nextflow.md` for which pipeline ref form to use today.

### Step 8: Present the sbatch command + post-submission guidance (do NOT submit)

Show the user everything they need before they leave to submit. Submitting is their call — never run `sbatch` on their behalf.

**The command:**
```
sbatch <run_dir>/submit_metascope.sh
```

**While the job is running:**
- Monitor: `squeue -u <netid>` — one array job with N tasks.
- Logs: `<log_dir>/slurm.<job-name>.<arrayid>_<task>.out` (per task).
- Pipeline outputs: under `<outdir>` per pipeline conventions.

**If something fails:**
- Single fetch task failed: re-submit just that index — `sbatch --array=<failed_index> submit_metascope.sh`.
- `prefetch: cannot connect`: the compute node lacks outbound network. `fastq-dump` on a node with connectivity, or pre-stage with `prefetch` from the login node.
- `disk full` after fetching a few SRRs: `--fastq-dir` is on a quota-limited filesystem — point it at scratch.
- Empty FASTQs: the run is access-controlled (dbGaP). The skill won't bypass authorization; check with NCBI.
- Files named `<SRR>.fastq.gz` even for paired-end: `--split-files` got dropped from the rendered template — re-render.
- Pipeline error: check the log for the failing task. **Do not edit the nf-core/metascopeprolifer pipeline source**; report bugs there. The skill's role ends at producing a valid submission.

## Outputs

The skill's deliverable is **one SLURM array script** plus the `sbatch` command. Specifically:

- `<run_dir>/submit_metascope.sh` — SLURM array (`#SBATCH --array=0-(N-1)`). Per-task, fetches one accession's FASTQ via `fastq-dump --split-files --gzip`, then runs the Nextflow pipeline over the full samplesheet.
- `<run_dir>/samplesheet.csv` — nf-core-format samplesheet (`sample,fastq_1,fastq_2`).
- The literal `sbatch <run_dir>/submit_metascope.sh` command shown to the user.

After the user runs `sbatch`:
- Pipeline outputs land in `<outdir>` (from `SLURM_directives.yaml`).
- Logs in `<log_dir>` as `slurm.<job-name>.<arrayid>_<task>.out`.
- A MultiQC HTML report under `<outdir>/multiqc/` per nf-core convention.

## References

| You need to know… | Read |
|--|--|
| MetaScope Nextflow pipeline params, samplesheet schema, version | `references/metascope-nextflow.md` |

