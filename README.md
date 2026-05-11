# MetaScope SLURM

> Claude Code skill that turns SRA accessions into a ready-to-`sbatch` SLURM array script for [nf-core/metascopeprolifer](https://github.com/hjfan527/nf-core-metascopeprolifer) on Rutgers Amarel.

## Tested agent CLIs

The skill format (`SKILL.md` + `/plugin marketplace`) is Claude Code-native, so other CLIs would need to invoke `scripts/*.py` manually rather than loading the skill as a plugin.

| CLI | Model family | Status |
| --- | --- | --- |
| [Claude Code](https://code.claude.com/docs/en/overview) | Claude (Opus 4.x, Sonnet 4.x) | ✅ Tested |
| [OpenAI Codex CLI](https://github.com/openai/codex) | GPT-5.x | Untested |
| [Gemini CLI](https://github.com/google-gemini/gemini-cli) | Gemini xx | Untested |

## Overview

Give the skill SRA accessions and a metadata table; it gives you back **one** SLURM array script for 16S / shotgun-metagenomic taxonomic profiling.

Under the hood the skill:

1. Expands non-run accessions (BioProject, SRX, GSE, …) into a runinfo CSV via NCBI E-utilities.
2. Builds an nf-core samplesheet from the run-level data.
3. Renders a SLURM array that fans `fastq-dump` across array tasks, then runs Nextflow once after every fetch finishes.

The skill never calls `sbatch` itself — it prints the command for you to run.

## Requirements

| | |
| --- | --- |
| Claude Code | install via Anthropic's [official guide](https://code.claude.com/docs/en/overview); verify with `claude --version`. |
| Python | ≥ 3.7 on the machine running the skill. |
| Reference databases | a Bowtie2 index, a BLAST database, and an `accessionTaxa.sql` file. See [`skills/metascope-slurm/references/metascope-nextflow.md`](skills/metascope-slurm/references/metascope-nextflow.md). |

## Install

Inside a Claude Code session, use the following command to install the skill:

```
/plugin marketplace add technophilic03/MetaScope-Skill
/plugin install metascope-slurm@Metascope_skill
```
Alternatively, you can install the skill interactively by entering `/plugin`, go to `Marketplaces` tab and `+ Add Marketplaces`, then enter `technophilic03/MetaScope-Skill` as the marketplace source.

## Update
To pick up updates of the skill later, you can run `/plugin marketplace update` or update interactively under the `Marketplaces` tab.

Note May/2026: Current Claude Code can load stale cached version of skills even though the user updated. To make sure the skill is up-to-date, prompt Claude NOT to use the stale version (e.g. `"Make sure the skill is updated. Self-check: DONOT use stale cached version"`). Fix in progress.


## Usage

Invoke the skill in natural language — for example:

```
/metascope-slurm generate an Amarel submission script for MetaScope on PRJNA242354. Choose 10 random runs.
```

**Inputs:**
- accession number(s)
- Metadata.csv from NCBI Run selector (optional)

Claude will walk you through six stages, regardless of where you're running:

| Stage | What happens |
| --- | --- |
| 1. Setup | Installs Python deps once into a dedicated venv inside the skill. |
| 2. Inputs | Collects accessions and the FASTQ destination (default `<run_dir>/fastq`). |
| 3. SLURM directives | Asks for partition, time, memory, scratch dir, etc. Cached at `./metascope-microbiome/SLURM_directives.yaml`. |
| 4. Database paths | Asks for Bowtie2 / BLAST / `accessionTaxa.sql` paths. Optionally cached at `./metascope-microbiome/databases.yaml`. |
| 5. Render + preflight | Produces `submit_metascope.sh` and runs static checks. |
| 6. You submit | The skill prints `sbatch submit_metascope.sh` for you to execute. |

```
Example database paths:

Bowtie2: /<path-to-project-or-home/reflib/2024_blast_16S_bt; 

Path to accessiontaxa: /<path-to-project-or-home/reflib/2024_accession_taxa 

BLAST: /<path-to-project-or-home/reflib/2024_blast_16S/16S_ribosomal_RNA  
# Important: the format is <folder_dir>/<basename>. basename is the file name without extension name (.ndb, .nhr, etc.)

Target index name: 16S_ribosomal_RNA
```

The skill itself only generates a script — the script always runs on Amarel. The two scenarios below differ only in *where you invoke Claude Code* and how the rendered script gets to the cluster.

### Scenario A: Running the skill on Amarel

Recommended. Everything happens on a single login node, and all the paths you give the skill (FASTQ dir, scratch, databases) are the same paths the SLURM job will see.

1. SSH into an Amarel login node (`ssh <netid>@amarel.rutgers.edu`) or use the Ondemand interface.
2. Start Claude Code in a project directory — your personal home directory is not recommanded because of quota limits.
3. Invoke the skill with inputs and optional requirements.
4. At step 8 of the skill, use the `sbatch` command provided to submit the script


### Scenario B: Running the skill on a personal computer

You generate the script locally, then transfer it to Amarel for submission.

1. Install Claude Code locally, ensure Python ≥ 3.7 is on your `PATH` (`python3 --version`), and have Nextflow installed.
2. Start Claude Code in any working directory and invoke the skill as shown above.
3. **When the skill asks for paths, give it the Amarel-side paths**, not local ones.
4. After Stage 5, transfer the rendered run directory to Amarel. Everything the job needs lives under the run dir (script, samplesheet, `runs.txt`, cached configs)
5. Submit

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `/plugin marketplace add` errors with `unknown option 'shallow-submodules'` | git < 2.9 | Pre-clone the repo and add it as a local marketplace (see below). |
| `/plugin install` fails with a glibc / SSH error | The loaded git module ships an `ssh` binary incompatible with the system glibc | Force HTTPS clones: `git config --global url.https://github.com/.insteadOf git@github.com:` |

**Local-marketplace fallback** for the first symptom:

```bash
git clone https://github.com/technophilic03/MetaScope-Skill.git ~/code/MetaScope-Skill
```

then inside Claude Code:

```
/plugin marketplace add ~/code/MetaScope-Skill
/plugin install metascope-slurm@Metascope_skill
```

## Further reading

- Full skill behavior and step-by-step workflow: [`skills/metascope-slurm/SKILL.md`](skills/metascope-slurm/SKILL.md)
- Pipeline parameters and reference-database preference order: [`skills/metascope-slurm/references/metascope-nextflow.md`](skills/metascope-slurm/references/metascope-nextflow.md)
