# Rutgers Amarel HPC reference

The skill targets **Rutgers Amarel** — the standard research HPC at Rutgers, operated by OARC (Office of Advanced Research Computing), running SLURM. Site-specific values (account format, partitions, modules, scratch paths) are NOT baked into the skill — the user supplies them once via `./metascope-microbiome/SLURM_directives.yaml` and they're reused on every invocation.

This file documents what's generic (writable from SLURM + Amarel's published conventions alone) versus what's site-specific (must come from the user's account on Amarel).

If the lab uses a Rutgers cluster other than Amarel, the SLURM directives in `assets/slurm_array.sh.j2` likely still apply (Amarel and most Rutgers clusters are SLURM); only the account/partition/module names differ. The other half of the skill (samplesheet builder, validators, registry, the `nextflow run nf-core/metascopeprolifer` line embedded in `submit_metascope.sh`) is scheduler- and cluster-agnostic.

## Authoritative source

Point the skill at Rutgers OARC's published documentation:

- Rutgers OARC user guide / Amarel quickstart (the user supplies the actual URL — keep it current here)
- Any group-specific PI quickstart on Amarel

Update this section with the URLs your lab actually uses.

## What the skill assumes about Amarel

| Aspect              | Assumption                                                                              | Confidence      |
|---------------------|-----------------------------------------------------------------------------------------|-----------------|
| Scheduler           | SLURM (`sbatch`, `#SBATCH`, `squeue`)                                                   | High (Amarel is SLURM) |
| Module system       | Lmod (`module load <name>`)                                                             | High            |
| FASTQ download      | `fastq-dump` from SRA Toolkit available via a `module load` (name TBD by user)          | Site-specific   |
| Nextflow            | Available via `module load`, conda env, or singularity image (one of three)             | Site-specific   |
| Profile naming      | `singularity,slurm` or `apptainer,slurm` is conventional but Amarel may use a custom name | Site-specific |
| Outbound network    | Login nodes can `git clone` / `nextflow pull`; compute nodes' connectivity varies       | Site-specific   |

If your cluster is **not** SLURM (PBS/Torque/etc.), the SLURM template in `assets/slurm_array.sh.j2` will not apply — file an issue and adapt. The rest of the skill (samplesheet builder, validators, registry, the `nextflow run nf-core/metascopeprolifer` invocation it embeds) is scheduler-agnostic.

## Generic vs. site-specific (the dividing line)

| Generic — baked into `assets/slurm_array.sh.j2`    | Site-specific — user fills in `SLURM_directives.yaml` |
|----------------------------------------------------|--------------------------------------------------------|
| `#SBATCH` directive syntax                         | `--account` value                                      |
| stdout/stderr filename pattern (`%x.%A_%a.out`)    | `--partition` value                                    |
| Job-name pattern                                   | `--time`, `--mem`, `--cpus-per-task` defaults          |
| Per-task fetch + marker-barrier flock pattern      | `module load` lines                                    |
| `nextflow run <ref>` invocation pattern            | nf-core profile (`singularity`, `singularity,slurm`)   |
| `fastq-dump --split-files --gzip` invocation       | Pipeline ref (canonical, GitHub, or local path)        |
| Samplesheet column names (from Howard's pipeline)  | Scratch / work / output / log directory paths          |

## How the skill collects these values

Two paths, used together or separately:

1. **Interactive** (default) — Claude asks the user one question per SKILL.md Step 2 field at run time, suggesting hints/options where possible (`partition`: `main`/`gpu`; `nextflow_profile`: `singularity`/`singularity,slurm`; …). Answers feed `render_submission.py` directly via CLI flags (`--account`, `--partition`, `--module-loads`, …).
2. **Cache** (recommended) — after the first interactive session, the skill offers to write the answers to `./metascope-microbiome/SLURM_directives.yaml`. On future runs, pass `--slurm-config ./metascope-microbiome/SLURM_directives.yaml` to seed defaults. Any individual value can still be overridden with a CLI flag for an ad-hoc run (e.g. `--partition gpu` to switch partitions for one run while keeping the rest of the cache).

The skill never silently reaches for a default value — every site-specific field is either supplied explicitly via CLI flag or read from a cache the user authored.

## Field reference

The user fills these (interactively or once into the cache YAML).

| Field               | Where to find it                                                                       |
|---------------------|----------------------------------------------------------------------------------------|
| `account`           | Rutgers SLURM `--account` for your group. Ask your PI or check `sshare -U`.            |
| `partition`         | Default partition for your group (e.g., `main`, `cmain`, `gpu`). Ask your PI.          |
| `default_time`      | Walltime for typical runs (e.g., `12:00:00`). Tune per dataset size.                   |
| `default_mem`       | Memory request (e.g., `200G`).                                                         |
| `default_cpus`      | CPUs per task (e.g., `16`).                                                            |
| `log_dir`           | SLURM stdout/stderr directory. Often `<scratch>/logs`.                                 |
| `module_loads`      | Multi-line list. Likely needs at minimum: `nextflow`, `java`, `singularity` (or `apptainer`), `sratoolkit`. |
| `nextflow_profile`  | Nextflow profile string. Often `singularity` or `singularity,slurm`. Confirm Rutgers' convention. |
| `pipeline_ref`      | `nf-core/metascopeprolifer` (canonical, once on nf-co.re), `hjfan527/nf-core-metascopeprolifer` (works today), a tagged commit SHA, or a local clone path. See `references/metascope-nextflow.md`. |
| `scratch_dir`       | Absolute path to your scratch (e.g., `/scratch/<netid>`). Used for FASTQ + Nextflow work dir. |
| `work_dir`          | Nextflow work directory (often `<scratch>/work`).                                      |
| `outdir`            | Pipeline `--outdir` (absolute, on a persistent filesystem).                            |
| `extra_pipeline_args` | Optional. Any additional `nextflow run` flags.                                       |

## Verification checklist (run once after filling SLURM_directives.yaml)

- [ ] `sshare -U` lists the value you put in `account`.
- [ ] `sinfo -p <partition>` shows the partition exists.
- [ ] `module avail <name>` finds each `module_loads` entry.
- [ ] `which fastq-dump` (after loading the SRA toolkit module) returns a path.
- [ ] `which nextflow` returns a path.
- [ ] `<scratch_dir>` is writable.
- [ ] You can submit a hello-world `sbatch` job to `<partition>` under `<account>`.

When all of these pass, the skill can render a real submission script for you.
