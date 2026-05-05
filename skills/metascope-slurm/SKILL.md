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

## Interaction style: use `AskUserQuestion` for choice-style prompts

When you need the user to pick from a discrete set of options — yes/no, "use cache vs override", "pick which cached database" — use Claude Code's `AskUserQuestion` tool rather than asking in plain chat. It surfaces options as buttons with a structured UI and auto-adds an "Other" option for free-text. Each call: 2–4 options per question, up to 4 questions per turn, mark the recommended choice with "(Recommended)" and put it first.

For free-text inputs (paths, SRA accessions, sample IDs, job-name strings, scratch paths) — just ask in chat. There's nothing to enumerate, and a structured UI would be friction.

When several choice-style questions are related (e.g. FASTQ storage location + cleanup choice in Step 1.3), batch them into a single `AskUserQuestion` call — fewer turns, less friction.

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
For first-time use, run this block:
```
if [ -d /projects/community/modulefiles ]; then
    export MODULEPATH=$MODULEPATH:/projects/community/modulefiles
    module load python
    module load nextflow
fi
bash scripts/setup.sh
```
`scripts/setup.sh` requires Python >= 3.7.  After deps install (`pyyaml`, `jinja2`), it creates a dedicated venv at `<skill-dir>/venv/`. Safe to re-run every session.

For subsequent steps, prefer invoking Python scripts via the venv's python directly (`./venv/bin/python3 scripts/...`).

### Step 1: Collect inputs
Ask the user for whichever of these isn't already in hand:
1. **Accessions** — pasted inline or a file path.
2. **Metadata CSV (optional)** — path to a Run Selector / runinfo CSV, if they already have one.
3. **FASTQ storage** — batch as one `AskUserQuestion` call with two questions:
   - **Where to store FASTQs?** Options: `Run dir: <run_dir>/fastq (Recommended)`, `Scratch: <scratch_dir>/<job_name>/fastq`. Use the answer as `--fastq-dir` in Steps 5 and 6 (both scripts must receive the same value).
   - **Remove FASTQs after the pipeline succeeds?** Options: `Keep FASTQs (Recommended)`, `Remove on success`. If the user picks remove, pass `--remove-fastq-after-run` to Step 6 — `rm -rf "$FASTQ_DIR"` is appended to the rendered SLURM script, gated on Nextflow success.

Then, before proceeding:
- If **any accession isn't a run** (SRR/ERR/DRR) and no run-level CSV was provided, run the eutils expansion (see "Expansion via E-utilities" above) and save the runinfo CSV to `<run_dir>/SraRunTable.csv`.
- If **only runs were provided** and no metadata was given, you can either skip metadata fetching (you only need `LibraryLayout` for the samplesheet — ask the user) or fetch via efetch over the runs themselves to populate the CSV.

By the time Step 3 begins, there is one run-level CSV at a known path.

### Step 2: Configure SLURM directives (ask the user)

Walk through these values one at a time. Use the data from Step 1 to suggest informed hints. Accept whatever the user provides.

**Cache handling — always ask the user, never silently apply.** If a saved cache exists at `./metascope-microbiome/SLURM_directives.yaml`, show its contents to the user, then use `AskUserQuestion` with three options: `Use as-is (Recommended)`, `Override specific fields`, `Start fresh`. Do not assume the user wants the cache — they may be running a different study, partition, or memory profile. After they decide, only walk through the fields they want to change (if any). For the field-by-field overrides, use plain chat (the inputs are free-text: time strings, integers, paths).

| Field          | YAML key         | CLI flag         | Hint / example                                                |
|----------------|------------------|------------------|---------------------------------------------------------------|
| Partition      | `partition`      | `--partition`    | Default: `main`                                               |
| Job name       | `job_name`       | `--job-name`     | Descriptive identifier, e.g., `metascope-run01`.              |
| Time           | `default_time`   | `--time`         | `HH:MM:SS`. Default `12:00:00`.                               |
| CPUs per task  | `default_cpus`   | `--cpus`         | Default `16`.                                                 |
| Memory         | `default_mem`    | `--mem`          | Default `200G`.                                               |
| Scratch dir    | `scratch_dir`    | `--scratch-dir`  | Absolute path, e.g. `/scratch/<netid>`.                       |
| Work dir       | `work_dir`       | `--work-dir`     | Absolute path; Nextflow work dir.                             |
| Outdir         | `outdir`         | `--outdir`       | Absolute path; pipeline results land here. Default `.`        |
| Log dir        | `log_dir`        | `--log-dir`      | Absolute path; SLURM stdout/stderr. Default `./logs`.         |

**Note on paths.** All path-style fields above (and the `--fastq-dir` / `--output-dir` / `--samplesheet` / `--runs-list` flags downstream) get absolutized at render time. Passing relative paths is *accepted* but resolved against the user's current working directory at the moment the renderer runs — which is rarely what they want. Prefer absolute paths everywhere. The renderer + builder both call `Path(...).resolve()` to normalize, so the rendered SLURM script's `cd "$WORK_DIR"` doesn't strand any relative reference.

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

1. **Check the cache and show its contents.** If `./metascope-microbiome/databases.yaml` exists, list its entries with their `type`:
   ```
   # Example
   Cached databases:
     silva_138 (16S)
     my_shotgun_db (shotgun)
   ```
   If the file does not exist, skip to step 3.

2. **Always ask the user, never auto-pick.** Use `AskUserQuestion` with one option per cached entry (e.g. `Use silva_138 (16S)`) plus a final `Supply a brand-new database` option. The tool caps at 4 options per question — if there are more than 3 cached entries, list the 3 most-likely-relevant and rely on the auto-added `Other` option for the rest. Even when only one cached entry exists, still ask — do not auto-pick.

3. **Collect paths** (when picking new, or no cache exists). Walk through one at a time; accept whatever the user provides:

   | Prompt                                          | CLI flag                |
   |-------------------------------------------------|-------------------------|
   | Database name (used as the cache key)           | (cache only)            |
   | Bowtie2 index directory                         | `--db-index-dir`        |
   | Target reference index name                     | `--db-target`           |
   | Optional host filter index name (blank to skip) | `--db-filter`           |
   | Path to `accessionTaxa.sql`                     | `--db-accession-path`   |
   | BLAST database **prefix** (no extension)        | `--db-blast-path`       |

   **BLAST DB path is a prefix, not a directory or a file.** A BLAST database is a set of files sharing a common basename — e.g., `16S_ribosomal_RNA.nhr`, `.nin`, `.nsq`, `.ndb` in directory `/path/to/2024_blast_16S/`. Pass the directory + basename without extension: `/path/to/2024_blast_16S/16S_ribosomal_RNA`. Passing just the directory, or pointing at a single `.nhr` file, will fail.

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
   Future runs can just pick the name from the cache.

   **If the user declines to save**, that's a clean code path — *do not* write a stub YAML to disk just to satisfy the renderer. In Step 6, omit `--db-config` entirely and pass all collected paths via `--db-*` flags. The renderer treats this as an ad-hoc run and uses `custom` as the database label in the rendered script's header comment.

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

**If the user picked from the database cache** (or saved a new entry to it) in Step 4:
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

**If the user declined to cache** : omit `--db-config` and `--database` entirely; supply the paths via `--db-*` flags instead:
```
python3 scripts/render_submission.py \
  --slurm-config ./metascope-microbiome/SLURM_directives.yaml \
  --db-index-dir <…> --db-target <…> \
  [--db-filter <…>] \
  --db-accession-path <…> --db-blast-path <…> \
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
- Preflight `samplesheet references files not on disk` for single-end runs pointing at `<SRR>.fastq.gz` (no `_1`): the samplesheet was built with an older `build_samplesheet.py` that predicted the wrong single-end name. `fastq-dump --split-files` emits `<SRR>_1.fastq.gz` even for single-end. Re-run Step 5 with the current builder, then re-submit (the fetch step is idempotent).
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

