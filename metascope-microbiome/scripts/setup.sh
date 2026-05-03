    #!/usr/bin/env bash
    # Install Python dependencies for the metascope-microbiome skill.
    #
    # Safe to run on every session start. Skips packages that are
    # already importable. Choice of where to install:
    #
    #   1. If $VIRTUAL_ENV is set  → install into that virtualenv
    #   2. Else                    → create/reuse a dedicated venv at
    #                                ~/.config/metascope-microbiome/venv (Default)
    #
    # Usage:    bash scripts/setup.sh
    # Override: PYTHON=/path/to/python3 bash scripts/setup.sh

    set -euo pipefail

    SKILL_HOME="${HOME}/.config/metascope-microbiome"
    SKILL_VENV="${SKILL_HOME}/venv"

    # pip-name : python-import-name pairs (some packages differ in the two)
    DEPS=(
        "pyyaml:yaml"
        "jinja2:jinja2"
        "pysradb:pysradb"
    )

    PYTHON="${PYTHON:-python3}"
    if ! command -v "$PYTHON" >/dev/null 2>&1; then
        echo "ERROR: '$PYTHON' not on PATH. Set PYTHON=<path-to-python3> and retry." >&2
        exit 1
    fi

    # -- Pick install target ------------------------------------------------------
    if [[ -n "${VIRTUAL_ENV:-}" ]]; then
        target_label="active virtualenv at $VIRTUAL_ENV"
        install_python="$PYTHON"
    else
        target_label="skill venv at $SKILL_VENV"
        if [[ ! -x "$SKILL_VENV/bin/python3" ]]; then
            mkdir -p "$SKILL_HOME"
            echo "Creating venv at $SKILL_VENV (one-time)…"
            "$PYTHON" -m venv "$SKILL_VENV"
        fi
        install_python="$SKILL_VENV/bin/python3"
    fi

    # -- Check what's missing -----------------------------------------------------
    check_import() {
        "$install_python" -c "import $1" 2>/dev/null
    }

    missing=()
    for entry in "${DEPS[@]}"; do
        pip_name="${entry%%:*}"
        import_name="${entry##*:}"
        if check_import "$import_name"; then
            echo "  ✓ $pip_name (import $import_name) available in $target_label"
        else
            missing+=("$pip_name")
        fi
    done

    if [[ ${#missing[@]} -eq 0 ]]; then
        echo "All dependencies already installed."
        if [[ "$target_label" == "skill venv at $SKILL_VENV" ]]; then
            echo
            echo "To use:  source $SKILL_VENV/bin/activate"
        fi
        exit 0
    fi

    # -- Install ------------------------------------------------------------------
    echo "Installing into $target_label: ${missing[*]}"
    "$install_python" -m pip install "${missing[@]}"

    # -- Verify -------------------------------------------------------------------
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
    if [[ "$target_label" == "skill venv at $SKILL_VENV" ]]; then
        echo
        echo "To use the skill venv:"
        echo "  source $SKILL_VENV/bin/activate"
        echo "Or invoke scripts with:"
        echo "  $SKILL_VENV/bin/python3 scripts/validate_inputs.py ..."
    fi
