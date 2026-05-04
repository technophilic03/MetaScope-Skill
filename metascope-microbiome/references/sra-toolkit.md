# SRA Toolkit reference

Howard's pipeline does not fetch FASTQ files from SRA — see `references/metascope-nextflow.md`. The skill therefore runs `fastq-dump` from the SRA Toolkit *before* invoking Nextflow, inside each array task of the SLURM submission script.

## What `fastq-dump` accepts

`fastq-dump` only takes **run** accessions (`SRR…`, `ERR…`, `DRR…`) directly. For experiments (`SRX…`), samples (`SRS…`), studies (`SRP…`), BioProjects (`PRJNA…`), or GEO entries (`GSE…`/`GSM…`), the validator (`scripts/validate_inputs.py`) expands the user's accession to its constituent runs *first*, using `pysradb` (see `references/metadata-schema.md`). Each SLURM array task in `submit_metascope.sh` only ever sees a single run accession, picked from `runs.txt` (produced by `scripts/build_samplesheet.py`) using `$SLURM_ARRAY_TASK_ID` as the line index.

So:
- User puts any accession type in `metadata.csv`.
- Validator expands to runs.
- SLURM array task `i` fetches run `i` from `runs.txt`. Tasks run in parallel.

This separation keeps the SLURM template simple (it doesn't need to know about pysradb or expansion logic) and parallelizes the fetch step across the cluster.

## Why fastq-dump (and not fasterq-dump)

Both work. `fastq-dump` is older, more widely available, and supported by every SRA Toolkit version; `fasterq-dump` is faster but has different flags and requires more scratch space. The skill uses `fastq-dump` for portability. If your Rutgers module provides only `fasterq-dump`, swap the invocation in `assets/slurm_array.sh.j2`.

## Invocation used by the skill

For each SRR in the user's list, the SLURM job runs:

```bash
fastq-dump --split-files --gzip --outdir "<FASTQ_DIR>" "<SRR>"
```

- `--split-files` — splits paired-end reads into `<SRR>_1.fastq.gz` and `<SRR>_2.fastq.gz`. For single-end input, only `<SRR>.fastq.gz` is produced.
- `--gzip` — compresses output. Howard's pipeline samplesheet schema requires `.fq.gz` or `.fastq.gz`.
- `--outdir <FASTQ_DIR>` — places files in the user's scratch directory.

The naming convention (`<SRR>_1.fastq.gz`, `<SRR>_2.fastq.gz`, `<SRR>.fastq.gz`) is what `scripts/build_samplesheet.py` predicts when constructing the pipeline samplesheet (see `references/metadata-schema.md`).

## Module loading on Rutgers

The user's `SLURM_directives.yaml` `module_loads` field must include the SRA Toolkit module. The exact name varies per cluster; common values:
- `sratoolkit`
- `sra-tools`
- `SRA-Toolkit`

Confirm with `module avail sra` on the login node. If the toolkit is not modulized, the user can install it via conda (`conda install -c bioconda sra-tools`) or download a pre-built binary from https://github.com/ncbi/sra-tools/wiki/01.-Downloading-SRA-Toolkit.

## First-time setup (one-time, per user)

The SRA Toolkit requires a one-time configuration to set its workspace:

```bash
vdb-config --interactive   # follow prompts to set the workspace dir
# OR, non-interactive:
mkdir -p ~/.ncbi
cat > ~/.ncbi/user-settings.mkfg <<'EOF'
/LIBS/GUID = "<UUID>"
/repository/user/main/public/root = "<scratch_dir>/sra_workspace"
EOF
```

The skill does not perform this setup. Users who haven't run it will see SRA Toolkit warnings on first use. Documenting it here so they know what to do.

## Known failure modes

| Failure | Likely cause | Fix |
|---------|--------------|-----|
| `prefetch: cannot connect` | No internet on compute nodes | Run `fastq-dump` on a node with outbound connectivity, or pre-stage with `prefetch` from the login node. |
| `disk full` after fetching a few SRRs | `--outdir` on a quota-limited filesystem | Point `<FASTQ_DIR>` at scratch, not home. |
| Empty FASTQ files | SRR run is restricted (dbGaP) | Check accession permissions; the skill won't bypass authorization. |
| Files named `<SRR>.fastq.gz` even for paired-end | `--split-files` was omitted | Verify the template wasn't edited to drop the flag. |

## Why this file exists separately

Keeping SRA-Toolkit notes out of `references/metascope-nextflow.md` lets future maintainers cleanly remove this file if Howard's pipeline gains an `nf-core/fetchngs` subworkflow — at that point the skill should drop the explicit fastq-dump step entirely.
