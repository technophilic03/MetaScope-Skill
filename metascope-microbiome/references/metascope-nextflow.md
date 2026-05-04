# MetaScope Nextflow pipeline reference

The MetaScope pipeline lives at https://github.com/hjfan527/nf-core-metascopeprolifer (branch `main`), authored by Howard Fan. It is an nf-core-templated Nextflow pipeline for "16S/metagenomic taxonomic prolifer" classification.

## Status (verified at construction time)

The pipeline is **under active development**. The README, default config, and test config all contain `TODO nf-core:` markers, and `conf/test.config` has `'TODO'` literal strings for every MetaScope-specific parameter. Treat the pipeline as a moving target — pin to a specific commit SHA in the user's `SLURM_directives.yaml` (`pipeline_ref` field) for reproducibility.

## Heads-up: `nf-core/metascopeprolifer` is not on the registry yet

The canonical `nextflow run nf-core/metascopeprolifer ...` command **will fail today** — the pipeline is not in the nf-core GitHub org or on the nf-co.re registry. SKILL.md's templates default to `nf-core/metascopeprolifer` because that's the form Howard's README aspires to; if your `nextflow run -preview` errors with "pipeline not found", switch `pipeline_ref` in `SLURM_directives.yaml` to `hjfan527/nf-core-metascopeprolifer` (form #2 below) and re-render. When the pipeline is accepted into nf-co.re, no change to the skill is needed — the canonical name will start resolving.

When compute nodes block outbound traffic, pre-pull on the login node:

```bash
nextflow pull hjfan527/nf-core-metascopeprolifer  # caches into ~/.nextflow/assets/
```

## Required Nextflow version

`>=25.04.0`

## Invocation

```bash
nextflow run <pipeline_ref> \
   -profile <docker|singularity|apptainer|...> \
   --input <samplesheet.csv> \
   --outdir <results_dir> \
   --metascope_index_dir <path> \
   --metascope_target <index_name> \
   --metascope_accession_path <path/to/accessionTaxa.sql> \
   --metascope_db_path <path/to/blast_db> \
   [--metascope_filter <optional_filter_index>]
```

`<pipeline_ref>` accepts (in order of preference):

1. **`nf-core/metascopeprolifer`** — the canonical nf-core shorthand. The pipeline's README is structured for this form (`README.md` line: `nextflow run nf-core/metascopeprolifer ...`). Currently this resolves only if the pipeline is published to the nf-core registry; verify with `nextflow pull nf-core/metascopeprolifer` once.
2. **`hjfan527/nf-core-metascopeprolifer`** — direct GitHub ref. Works today since that's where the code lives. Use this until the pipeline is officially on nf-core.re.
3. **`hjfan527/nf-core-metascopeprolifer -r <commit-sha>`** — pinned to a specific revision. Recommended for reproducibility.
4. **`/projects/<lab>/code/nf-core-metascopeprolifer`** — local clone. Use when login nodes lack outbound git access or for offline runs.

The user's `SLURM_directives.yaml` `pipeline_ref` field decides which form gets baked into the submission script.

## Parameters (verbatim from `nextflow_schema.json`)

### Input/output (required)

| Param      | Type            | Notes                                                                                |
|------------|-----------------|--------------------------------------------------------------------------------------|
| `--input`  | CSV file path   | Samplesheet (see schema below). Must end in `.csv`. Comma-separated, with header.    |
| `--outdir` | Directory path  | Pipeline output. Must be absolute on cloud infrastructure.                           |

### MetaScope (4 required, 1 optional)

| Param                        | Required | Notes                                                                  |
|------------------------------|----------|------------------------------------------------------------------------|
| `--metascope_index_dir`      | yes      | Directory containing Bowtie2 indices.                                  |
| `--metascope_target`         | yes      | Name of the target reference index.                                    |
| `--metascope_filter`         | no       | Name of the filter reference index (host filter).                      |
| `--metascope_accession_path` | yes      | Path to `accessionTaxa.sql` for taxonomic ID.                          |
| `--metascope_db_path`        | yes      | Path to BLAST database for `metascope_blast` secondary classification. |

**Implication for "database flexibility":** a single user-facing database choice (e.g. `silva_138`, `rat_explorer`) maps to a *set* of these 5 paths, not a single value. See `./metascope-microbiome/databases.yaml` (the cache the skill writes per SKILL.md Step 4) and `assets/databases_template.yaml` for the schema.

### Reference genome

Standard nf-core: `--genome` (iGenomes ID), `--fasta` (custom FASTA), `--igenomes_ignore`, `--igenomes_base`. Not strictly required for MetaScope-only runs.

### Profiles defined in `nextflow.config`

`conda`, `mamba`, `docker`, `singularity`, `apptainer`, `podman`, `shifter`, `charliecloud`, `wave`, `gpu`, `arm64`, `emulate_amd64`, `test`, `test_full`. **No SLURM profile is baked in** — Rutgers requires either an institutional config from `nf-core/configs` or a custom `-c` config supplied by the user (see `references/rutgers-hpc.md`).

## Samplesheet schema (verbatim from `assets/schema_input.json`)

3 columns, with header row:

| Column    | Required | Constraints                                                              |
|-----------|----------|--------------------------------------------------------------------------|
| `sample`  | yes      | No spaces (`^\S+$`). Used as the sample ID downstream.                   |
| `fastq_1` | yes      | Path to FASTQ for read 1. Must end in `.fq.gz` or `.fastq.gz`. Must exist. |
| `fastq_2` | no       | Path to FASTQ for read 2. Same extension/path rules. Empty for single-end. |

Example (verbatim from `assets/samplesheet.csv`):

```csv
sample,fastq_1,fastq_2
SAMPLE_PAIRED_END,/path/to/fastq/files/AEG588A1_S1_L002_R1_001.fastq.gz,/path/to/fastq/files/AEG588A1_S1_L002_R2_001.fastq.gz
SAMPLE_SINGLE_END,/path/to/fastq/files/AEG588A4_S4_L003_R1_001.fastq.gz,
```

## Does the pipeline fetch FASTQ from SRA?

**No.** `main.nf` includes only `METASCOPEPROLIFER`, `PIPELINE_INITIALISATION`, `PIPELINE_COMPLETION` — no `nf-core/fetchngs` subworkflow, and the samplesheet schema demands existing local `.f[ast]q.gz` files. **The skill must run `fastq-dump` (SRA Toolkit) before invoking the pipeline.** See `references/sra-toolkit.md`.

## Resources (default from `conf/test.config`)

`cpus: 4, memory: '15.GB', time: '1.h'` — sufficient for the test profile only. Real runs need significantly more; the user supplies these values via `SLURM_directives.yaml`.

## Re-run this research when…

- Howard adds `nf-core/fetchngs` to the workflow → drop the per-task `fastq-dump` step in `assets/slurm_array.sh.j2`.
- The `TODO` markers in `conf/test.config` are filled in → adopt the resulting paths as a working `silva_138`-like default in `assets/databases_template.yaml`.
- The samplesheet schema gains columns (e.g. `strandedness`, `library_layout`) → update `scripts/build_samplesheet.py` and `assets/samplesheet_template.csv`.
