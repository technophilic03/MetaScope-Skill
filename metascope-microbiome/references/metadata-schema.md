# Metadata schema reference

The user supplies two pieces of information: an **accession list** (file or inline string) and a **metadata CSV**. This document specifies the accepted formats, the accession types, and how the validator's expansion step maps user-facing accessions to runs.

## Accession list — two input modes

Same logical input, two ways to provide it:

| Mode    | Flag                                  | When to use                                                               |
|---------|---------------------------------------|---------------------------------------------------------------------------|
| File    | `--accessions-file <path>`            | User has a saved file (e.g. SRA Run Selector "Accessions" download).      |
| Inline  | `--accessions-inline "<csv\|spaced>"` | User pastes a list in chat — no need to manufacture a file just for the tool. |

Both accept comments (`#…`) and treat blanks/separators identically. The two modes are mutually exclusive — pick one.

## Metadata CSV — two formats (auto-detected)

The validator inspects the header row and picks one of two formats. Both produce the same downstream `expanded_metadata.csv` and samplesheet.

### Format A: Standard (skill-native)

| Column            | Required | Constraints                                                                       |
|-------------------|----------|-----------------------------------------------------------------------------------|
| `sample_id`       | yes      | No spaces. Used as `sample` in the pipeline samplesheet.                          |
| `accession`       | yes      | Any SRA accession type (see table below).                                         |
| `library_layout`  | yes      | `single` or `paired`.                                                             |
| `sample_type`     | no       | Free text (e.g., `gut`, `oral`, `skin`).                                          |

Extra columns are accepted and pass through unchanged. Template: `assets/metadata_template.csv`.

### Format B: SRA Run Selector (download unchanged from NCBI)

Download from https://www.ncbi.nlm.nih.gov/Traces/study/ — the validator reads it directly. Detected by the presence of `Run` + `LibraryLayout` columns. Mapped:

| SRA Run Selector column | Maps to (standard)  | Notes                                                                  |
|-------------------------|---------------------|------------------------------------------------------------------------|
| `Run`                   | `accession`         | Each row is already a run, so expansion is a no-op for this format.    |
| `LibraryLayout`         | `library_layout`    | Lowercased (`PAIRED` → `paired`, `SINGLE` → `single`).                 |
| `Sample Name`           | `sample_id`         | Falls back to `BioSample`, then `Run`, if `Sample Name` is empty. Spaces normalized to underscores. |

All other columns (`BioProject`, `Experiment`, `Instrument`, `MBases`, …) pass through. Multiple `Run` rows sharing the same `Sample Name` produce multiple samplesheet rows under the same `sample` — see "Multi-run-per-sample" below.

### Format mismatch

If the header matches neither format, the validator surfaces the recognized headers and asks the user to either rename columns to the Standard format or use a fresh SRA Run Selector export.

## Accepted accession types

The validator (`scripts/validate_inputs.py`) recognizes:

| Kind          | Pattern              | Examples              | Direct to fastq-dump? |
|---------------|----------------------|-----------------------|-----------------------|
| run           | `^[SED]RR\d+$`       | `SRR12345`, `ERR9876` | yes                   |
| experiment    | `^[SED]RX\d+$`       | `SRX1234567`          | no — has 1+ runs      |
| sample        | `^[SED]RS\d+$`       | `SRS5678901`          | no                    |
| study         | `^[SED]RP\d+$`       | `SRP100200`           | no                    |
| GEO series    | `^GSE\d+$`           | `GSE123456`           | no                    |
| GEO sample    | `^GSM\d+$`           | `GSM7890123`          | no                    |
| BioProject    | `^PRJ(NA|EB|DB)\d+$` | `PRJNA12345`          | no                    |

Non-run accessions are expanded via `pysradb` to their constituent runs. The downstream scripts only ever see run accessions.

## Multi-run-per-sample

The same `sample_id` legitimately appears on multiple metadata rows when:
- An SRX (or any non-run) accession in the Standard format expands to several runs, OR
- An SRA Run Selector export has multiple `Run` rows sharing the same `Sample Name` (one biosample, multiple lanes/runs).

The validator therefore allows duplicate `sample_id` *as long as the (`sample_id`, `accession`) pair is unique*. The resulting samplesheet has the same `sample` repeated — Howard's nf-core pipeline merges them per sample at the relevant stage.

What's still rejected:
- Duplicate accessions across rows (each accession should appear at most once).
- Identical (`sample_id`, `accession`) pairs (truly redundant rows).

## Mapping to Howard's samplesheet

Howard's pipeline samplesheet has 3 columns: `sample,fastq_1,fastq_2` (see `references/metascope-nextflow.md`). The flow is:

```
metadata.csv                    expanded_metadata.csv             samplesheet.csv
─────────────                   ─────────────────────             ───────────────
sample_id        ──────────►   sample_id           ──────────►   sample
accession (any)  ──pysradb──►  run_accession                     fastq_1, fastq_2
                              (1 row may → N rows)                (predicted from run)
library_layout   ──────────►   library_layout
```

If one input accession (e.g. `SRX1234567`) expands to N runs, the expanded metadata has N rows for the same `sample_id`. The samplesheet then has N rows with the same `sample` value but different fastq paths — **this is the standard nf-core "multiple lanes per sample" pattern**. Howard's pipeline merges rows with the same `sample` value at the appropriate stage.

**Path prediction** for `fastq-dump --split-files --gzip --outdir <fastq_dir> <RUN>`:

| Library layout | Files produced                                                  |
|----------------|-----------------------------------------------------------------|
| `single`       | `<fastq_dir>/<RUN>.fastq.gz`                                    |
| `paired`       | `<fastq_dir>/<RUN>_1.fastq.gz`, `<fastq_dir>/<RUN>_2.fastq.gz`  |

The samplesheet builder uses these conventions to populate paths *before* the SLURM job runs `fastq-dump`. The actual files appear after submission.

## Validation rules (`scripts/validate_inputs.py`)

The validator surfaces **every error at once**:

1. Every entry in the accession list (file or inline) matches one of the recognized patterns.
2. Every accession in the list appears in metadata exactly once; every metadata row has a corresponding list entry.
3. `sample_id` is non-empty and matches `^\S+$` (no spaces). Duplicates are allowed when paired with different accessions (multi-run pattern); identical (`sample_id`, `accession`) pairs are rejected.
4. `library_layout` (or `LibraryLayout` for SRA Run Selector format, after lowercasing) is `single` or `paired`.
5. The metadata CSV header matches one of the two recognized formats.
6. For non-run accessions, `pysradb` returns at least one run.

## Example

`metadata.csv`:
```csv
sample_id,accession,library_layout,sample_type
patient001_baseline,SRR12345678,paired,gut
patient002_followup,SRX9876543,paired,gut
patient003_baseline,GSM4567890,single,oral
```

`accession_list.txt`:
```
SRR12345678
SRX9876543
GSM4567890
```

After `validate_inputs.py --output expanded_metadata.csv` (suppose `SRX9876543` has 2 runs `SRR98760A` and `SRR98760B`, and `GSM4567890` has 1 run `SRR45678901`):

```csv
sample_id,run_accession,library_layout
patient001_baseline,SRR12345678,paired
patient002_followup,SRR98760A,paired
patient002_followup,SRR98760B,paired
patient003_baseline,SRR45678901,single
```

After `build_samplesheet.py`:

`samplesheet.csv`:
```csv
sample,fastq_1,fastq_2
patient001_baseline,/scratch/<netid>/fastq/SRR12345678_1.fastq.gz,/scratch/<netid>/fastq/SRR12345678_2.fastq.gz
patient002_followup,/scratch/<netid>/fastq/SRR98760A_1.fastq.gz,/scratch/<netid>/fastq/SRR98760A_2.fastq.gz
patient002_followup,/scratch/<netid>/fastq/SRR98760B_1.fastq.gz,/scratch/<netid>/fastq/SRR98760B_2.fastq.gz
patient003_baseline,/scratch/<netid>/fastq/SRR45678901.fastq.gz,
```

`runs.txt`:
```
SRR12345678
SRR98760A
SRR98760B
SRR45678901
```

`patient002_followup` appears twice in the samplesheet — Howard's pipeline merges these at the relevant stage. `runs.txt` drives the SLURM job's `fastq-dump` loop.
