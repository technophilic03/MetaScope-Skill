# MetaScope SLURM

Claude Code skill that generates Rutgers Amarel SLURM submission scripts wrapping [nf-core/metascopeprolifer](https://github.com/hjfan527/nf-core-metascopeprolifer) for 16S / shotgun-metagenomic taxonomic profiling.

Given SRA accessions and a metadata table, the skill:
1. Fetches a runinfo CSV via NCBI E-utilities when the user provides non-run accessions (BioProject, SRX, GSE, …).
2. Builds an nf-core samplesheet from the run-level data.
3. Renders **one SLURM array script** that fans `fastq-dump` across array tasks, then runs Nextflow once after all fetches complete.

## Requirements

- **Claude Code** — see "Install Claude Code" below.
- **Python ≥ 3.7** on the machine where Claude Code runs the skill (e.g. an Amarel login node). The skill auto-loads via `module load python` if the system `python3` is missing or too old.
- **Pre-built reference databases** — a Bowtie2 index, a BLAST database, and an `accessionTaxa.sql` file. See `skills/metascope-slurm/references/metascope-nextflow.md` for what each one is.
- Rutgers Amarel access (or another SLURM cluster — directives may need adaptation).

## Install Claude Code

Follow Anthropic's official instructions: <https://docs.claude.com/en/docs/agents-and-tools/claude-code>. Verify with `claude --version`.

## Install this skill

Inside a Claude Code session:

```
/plugin marketplace add technophilic03/MetaScope-Skill
/plugin install metascope-slurm@Metascope_skill
```

**If `/plugin marketplace add` errors with `unknown option 'shallow-submodules'`** (older git, < 2.9), pre-clone the repo and add it as a local marketplace:

```bash
git clone https://github.com/technophilic03/MetaScope-Skill.git ~/code/MetaScope-Skill
```

then inside Claude Code:

```
/plugin marketplace add ~/code/MetaScope-Skill
/plugin install metascope-slurm@Metascope_skill
```

**If `/plugin install` fails with a glibc / SSH error**, the loaded git module brought along an `ssh` binary that's incompatible with the system glibc. Force HTTPS clones:

```bash
git config --global url.https://github.com/.insteadOf git@github.com:
```

## Run it

Describe your run to Claude in natural language. For example:

```
generate an Amarel submission script for MetaScope on PRJNA242354. Choose 10 random runs.
```

Claude will walk you through:

1. **Setup** — installs Python deps once, into a dedicated venv inside the skill.
2. **Inputs** — your accessions; where to put the downloaded FASTQs (default `<run_dir>/fastq`).
3. **SLURM directives** — partition, time, memory, scratch dir, etc. Cached at `./metascope-microbiome/SLURM_directives.yaml` for re-use.
4. **Database paths** — Bowtie2 index, BLAST DB, accessionTaxa.sql. Optionally cached at `./metascope-microbiome/databases.yaml`.
5. **Render + preflight** — produces `submit_metascope.sh` with full static checks.
6. **You run** `sbatch submit_metascope.sh`.

The skill never submits the job for you — it produces the script and shows the `sbatch` command.

## Updating the skill

When new commits land in this repo:

```
/plugin marketplace update
```

then re-install if needed.

## More

- Full skill behavior + workflow steps: `skills/metascope-slurm/SKILL.md`
- Pipeline parameters and ref preference order: `skills/metascope-slurm/references/metascope-nextflow.md`
