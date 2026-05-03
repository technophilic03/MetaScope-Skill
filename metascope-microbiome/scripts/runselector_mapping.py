"""
Map a pysradb metadata row (detailed=True) onto SRA Run Selector column names.

Hypothesis verified by tests/test_pysradb_runselector_format.py: pysradb's
detailed metadata is the same data as a Run Selector CSV download — only the
column names differ. This module encodes the mapping.

  - DIRECT_MAP: Run Selector column → pysradb column (1:1)
  - DERIVED:    Run Selector column → callable that derives the value from a
                pysradb row (used for AvgSpotLen, DATASTORE provider/region,
                and Sample Name, which require composition or computation)
  - GAPS:       Run Selector columns with no pysradb source. Left blank in
                the output. Filling them would require an extra eutils round
                trip; they are not used by Howard's nf-core pipeline so the
                cost is not justified for this skill.
  - RUNSELECTOR_COLUMNS: the canonical column order to emit, matching the
                order shown by SRA Run Selector's default download.

Use map_pysradb_row_to_runselector(p_row) to convert one row.
"""
from __future__ import annotations

from typing import Callable


def _present(v) -> bool:
    if v is None:
        return False
    s = str(v).strip()
    return s != "" and s.lower() != "nan" and s != "<NA>"


def _clean(v) -> str:
    if not _present(v):
        return ""
    return str(v).strip()


def _to_int(v) -> int:
    if not _present(v):
        return 0
    try:
        return int(float(str(v).strip()))
    except (ValueError, TypeError):
        return 0


# ---- Run Selector column → pysradb column (1:1) ---------------------------
DIRECT_MAP: dict[str, str] = {
    "Run":                  "run_accession",
    "AGE":                  "age",
    "Assay Type":           "library_strategy",
    "Bases":                "run_total_bases",
    "BIOMATERIAL_PROVIDER": "biomaterial_provider",
    "BioProject":           "bioproject",
    "BioSample":            "biosample",
    "BioSampleModel":       "biosamplemodel",
    "Bytes":                "total_size",
    "Experiment":           "experiment_accession",
    "Instrument":           "instrument",
    "isolate":              "isolate",
    "LibraryLayout":        "library_layout",
    "LibrarySelection":     "library_selection",
    "LibrarySource":        "library_source",
    "Organism":             "organism_name",
    "Platform":             "instrument_model_desc",
    "version":              "public_version",
    "sex":                  "sex",
    "SRA Study":            "study_accession",
    "tissue":               "tissue",
}


# ---- Derived columns ------------------------------------------------------
def derive_avg_spot_len(p: dict) -> str:
    spots = _to_int(p.get("run_total_spots"))
    bases = _to_int(p.get("run_total_bases"))
    if not spots:
        return ""
    return str(bases // spots)


def derive_datastore_provider(p: dict) -> str:
    parts = []
    if _present(p.get("gcp_url")):
        parts.append("gs")
    if _present(p.get("ncbi_url")):
        parts.append("ncbi")
    if _present(p.get("aws_url")):
        parts.append("s3")
    return ",".join(parts)


def derive_datastore_region(p: dict) -> str:
    # SRA's per-provider regions are constant. pysradb's *_free_egress fields
    # sometimes return "-" or blank, so use the constants when the matching
    # URL is present.
    parts = []
    if _present(p.get("gcp_url")):
        parts.append("gs.us-east1")
    if _present(p.get("ncbi_url")):
        parts.append("ncbi.public")
    if _present(p.get("aws_url")):
        parts.append("s3.us-east-1")
    return ",".join(parts)


def derive_sample_name(p: dict) -> str:
    # Run Selector's "Sample Name" maps to the run/experiment alias for most
    # submissions. Fall back through related fields, then run accession.
    for key in ("run_alias", "experiment_alias", "sample_title", "library_name"):
        v = _clean(p.get(key))
        if v:
            return v
    return _clean(p.get("run_accession"))


DERIVED: dict[str, Callable[[dict], str]] = {
    "AvgSpotLen":         derive_avg_spot_len,
    "DATASTORE provider": derive_datastore_provider,
    "DATASTORE region":   derive_datastore_region,
    "Sample Name":        derive_sample_name,
}


# ---- Known gaps (Run Selector columns absent from pysradb) ---------------
# Reasons documented in the test file. Filled with empty strings so the
# CSV header still matches a Run Selector download exactly.
GAPS: dict[str, str] = {
    "Center Name":        "Submitting center; not in pysradb (try eutils <Center>).",
    "Consent":            "Always 'public' for SRA-public records; not in pysradb.",
    "DATASTORE filetype": "List of available file types; not in pysradb.",
    "ReleaseDate":        "Public release date of the run; not in pysradb output.",
    "create_date":        "Submission timestamp; not in pysradb output.",
}


# Canonical column order: matches SRA Run Selector's default download header.
RUNSELECTOR_COLUMNS: list[str] = [
    "Run", "AGE", "Assay Type", "AvgSpotLen", "Bases",
    "BIOMATERIAL_PROVIDER", "BioProject", "BioSample", "BioSampleModel",
    "Bytes", "Center Name", "Consent",
    "DATASTORE filetype", "DATASTORE provider", "DATASTORE region",
    "Experiment", "Instrument", "isolate", "LibraryLayout",
    "LibrarySelection", "LibrarySource", "Organism", "Platform",
    "ReleaseDate", "create_date", "version",
    "Sample Name", "sex", "SRA Study", "tissue",
]


def map_pysradb_row_to_runselector(p_row: dict) -> dict:
    """Convert one pysradb (detailed=True) row to a Run Selector-shaped dict.

    Output dict keys are the Run Selector column names (in RUNSELECTOR_COLUMNS
    order is the caller's responsibility). Values are strings; empty string
    means missing/unavailable.
    """
    out: dict[str, str] = {}
    for rs_col, py_col in DIRECT_MAP.items():
        out[rs_col] = _clean(p_row.get(py_col))
    for rs_col, fn in DERIVED.items():
        out[rs_col] = fn(p_row).strip()
    for rs_col in GAPS:
        out[rs_col] = ""  # no source in pysradb
    return out
