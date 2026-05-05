#!/usr/bin/env bash
# Install Python dependencies for the metascope-microbiome skill.
#
# Safe to run on every session start. Skips packages that are already
# importable. Choice of where to install:
#
#   1. If $VIRTUAL_ENV is set  -> install into that virtualenv
#   2. Else                    -> create/reuse a dedicated venv at
#                                 <skill-dir>/venv  (i.e. ./metascope-microbiome/venv)
#
#
# Usage:    bash scripts/setup.sh
# Override: PYTHON=/path/to/python3 bash scripts/setup.sh

set -euo pipefail

# Anchor paths to the skill directory.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(dirname "$SCRIPT_DIR")"
SKILL_VENV="$SKILL_DIR/venv"


DEPS=(
    "pyyaml:yaml"
    "jinja2:jinja2"
)

PYTHON="${PYTHON:-python3}"

# Returns 0 iff $PYTHON is on PATH AND its version is >= 3.7. The 3.7 floor
# comes from `from __future__ import annotations`, used across this skill's
# scripts; older Python errors with a SyntaxError at parse time.
check_py_ok() {
    command -v "$PYTHON" >/dev/null 2>&1 \
        && "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 7) else 1)' 2>/dev/null
}

# If $PYTHON is missing OR too old, try `module load python` once (Rutgers
# Amarel convention) before giving up. If `module` isn't a shell function, the user isn't on HPC and we
# go straight to error.
if ! check_py_ok; then
    cur_ver="(not on PATH)"
    if command -v "$PYTHON" >/dev/null 2>&1; then
        cur_ver=$("$PYTHON" --version 2>&1 || echo unknown)
    fi
    if type module >/dev/null 2>&1; then
        echo "Need Python >= 3.7 (got: $cur_ver). Trying 'module load python'..."
        module load python >/dev/null 2>&1 || true
    fi

    if ! check_py_ok; then
        cur_ver="(not on PATH)"
        if command -v "$PYTHON" >/dev/null 2>&1; then
            cur_ver=$("$PYTHON" --version 2>&1 || echo unknown)
        fi
        echo "ERROR: '$PYTHON' is missing or older than 3.7 (got: $cur_ver)." >&2
        echo "  - On HPC: run 'module avail python' to find a 3.7+ module," >&2
        echo "    then 'module load <name>' (or set PYTHON=<path>) and re-run." >&2
        echo "  - Otherwise: install Python >= 3.7, or set PYTHON=<path-to-python3.7+>." >&2
        exit 1
    fi
fi

echo "Using $("$PYTHON" --version 2>&1) at $("$PYTHON" -c 'import sys; print(sys.executable)')"

# -- Pick install target -----------------------------------------------------
if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    target_label="active virtualenv at $VIRTUAL_ENV"
    install_python="$PYTHON"
else
    target_label="skill venv at $SKILL_VENV"
    if [[ ! -x "$SKILL_VENV/bin/python3" ]]; then
        echo "Creating venv at $SKILL_VENV (one-time)..."
        "$PYTHON" -m venv "$SKILL_VENV"
    fi
    install_python="$SKILL_VENV/bin/python3"
fi

# -- Check what's missing ----------------------------------------------------
check_import() {
    "$install_python" -c "import $1" 2>/dev/null
}

missing=()
for entry in "${DEPS[@]}"; do
    pip_name="${entry%%:*}"
    import_name="${entry##*:}"
    if check_import "$import_name"; then
        echo "  OK  $pip_name (import $import_name) available in $target_label"
    else
        missing+=("$pip_name")
    fi
done

if [[ ${#missing[@]} -eq 0 ]]; then
    echo "All dependencies already installed."
    if [[ -z "${VIRTUAL_ENV:-}" ]]; then
        echo
        echo "To use the skill venv from the skill directory:"
        echo "  source ./venv/bin/activate"
        echo "Or from the parent of metascope-microbiome:"
        echo "  source ./metascope-microbiome/venv/bin/activate"
    fi
    exit 0
fi

# -- Install -----------------------------------------------------------------
# Upgrade pip first. Amarel's `module load python` (core 3.8.2) bundles an
# ancient pip (19.2.3, 2019-vintage) that fails on modern PEP 517 sdists with
# `BackendUnavailable`. A modern pip handles the same install fine.
echo "Upgrading pip in $target_label..."
"$install_python" -m pip install --upgrade pip --quiet

echo "Installing into $target_label: ${missing[*]}"
"$install_python" -m pip install "${missing[@]}"

# -- Verify ------------------------------------------------------------------
fails=()
for entry in "${DEPS[@]}"; do
    pip_name="${entry%%:*}"
    import_name="${entry##*:}"
    for m in "${missing[@]}"; do
        if [[ "$m" == "$pip_name" ]]; then
            if ! check_import "$import_name"; then
                fails+=("$pip_name (import $import_name)")
            fi
            break
        fi
    done
done

if [[ ${#fails[@]} -gt 0 ]]; then
    echo "ERROR: post-install import check failed for: ${fails[*]}" >&2
    echo "Try running this script inside a virtualenv, or check your pip configuration." >&2
    exit 1
fi

echo "All dependencies installed and importable."
if [[ -z "${VIRTUAL_ENV:-}" ]]; then
    echo
    echo "To use the skill venv from the skill directory:"
    echo "  source ./venv/bin/activate"
    echo "Or from the parent of metascope-microbiome:"
    echo "  source ./metascope-microbiome/venv/bin/activate"
    echo "Or invoke scripts directly with:"
    echo "  $SKILL_VENV/bin/python3 scripts/validate_inputs.py ..."
fi
