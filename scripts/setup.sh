#!/usr/bin/env bash
# =============================================================================
# Gemini Notebook by Chryzalide — Automated Installation & Setup Workflow
# =============================================================================
# Usage:
#   ./scripts/setup.sh            # Full setup (interactive auth prompt if TTY)
#   ./scripts/setup.sh --login    # Full setup + launch Google NotebookLM login
#   ./scripts/setup.sh --check    # Non-interactive setup & health verification
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
AGY_PLUGINS_DIR="${HOME}/.gemini/config/plugins"
AGY_PLUGIN_LINK="${AGY_PLUGINS_DIR}/gemini-notebook"

RUN_LOGIN=0
CHECK_ONLY=0
for arg in "$@"; do
    case "${arg}" in
        --login) RUN_LOGIN=1 ;;
        --check|--non-interactive) CHECK_ONLY=1 ;;
    esac
done

echo "======================================================================"
echo " 📓 Gemini Notebook by Chryzalide — Setup Workflow (v1.0.3)"
echo "======================================================================"
echo ""

# -----------------------------------------------------------------------------
# Step 1/5: Python Virtual Environment & Dependencies
# -----------------------------------------------------------------------------
echo "[Step 1/5] Checking Python environment and dependencies..."
if [[ -x "${PLUGIN_DIR}/.venv/bin/python" ]]; then
    echo "  ✓ Found existing virtual environment at ${PLUGIN_DIR}/.venv"
elif command -v uv >/dev/null 2>&1; then
    echo "  → Creating virtual environment and syncing packages with uv..."
    (cd "${PLUGIN_DIR}" && uv sync --all-extras)
elif [[ -x "${HOME}/.local/bin/uv" ]]; then
    echo "  → Creating virtual environment and syncing packages with ~/.local/bin/uv..."
    (cd "${PLUGIN_DIR}" && "${HOME}/.local/bin/uv" sync --all-extras)
else
    echo "  → Creating virtual environment with python3 -m venv..."
    python3 -m venv "${PLUGIN_DIR}/.venv"
    "${PLUGIN_DIR}/.venv/bin/pip" install --upgrade pip
    "${PLUGIN_DIR}/.venv/bin/pip" install -e "${PLUGIN_DIR}[rpc,dev]"
fi

VENV_PY="${PLUGIN_DIR}/.venv/bin/python"
VENV_CLI="${PLUGIN_DIR}/.venv/bin/agy-gemini-notebook"
echo "  ✓ Python package ready (${VENV_PY})"
echo ""

# -----------------------------------------------------------------------------
# Step 2/5: Playwright Chromium Browser Check (for Engine 2 Login)
# -----------------------------------------------------------------------------
echo "[Step 2/5] Checking Playwright Chromium browser for Google sign-in..."
EXISTING_CHROME="$(find "${HOME}/.cache/ms-playwright" -maxdepth 3 -name "chrome" -type f 2>/dev/null | head -n 1 || true)"
if [[ -n "${EXISTING_CHROME}" ]]; then
    echo "  ✓ Found installed Playwright Chromium binary (${EXISTING_CHROME})"
else
    if [[ "${CHECK_ONLY}" -eq 0 ]]; then
        echo "  → Installing Playwright Chromium browser..."
        "${PLUGIN_DIR}/.venv/bin/playwright" install chromium
        echo "  ✓ Playwright Chromium installed"
    else
        echo "  ! Playwright Chromium not found in ~/.cache/ms-playwright (run: .venv/bin/playwright install chromium)"
    fi
fi
echo ""

# -----------------------------------------------------------------------------
# Step 3/5: Antigravity 2 Plugin Registration & Live Enablement
# -----------------------------------------------------------------------------
echo "[Step 3/5] Registering plugin in Google Antigravity 2..."
mkdir -p "${AGY_PLUGINS_DIR}"
if [[ -L "${AGY_PLUGIN_LINK}" || -d "${AGY_PLUGIN_LINK}" ]]; then
    echo "  ✓ Plugin link present at ${AGY_PLUGIN_LINK}"
else
    ln -s "${PLUGIN_DIR}" "${AGY_PLUGIN_LINK}"
    echo "  ✓ Created symlink: ${AGY_PLUGIN_LINK} -> ${PLUGIN_DIR}"
fi

if [[ -n "${ANTIGRAVITY_LS_ADDRESS:-}" && -n "${ANTIGRAVITY_CSRF_TOKEN:-}" ]]; then
    curl -sS -X POST \
        "http://${ANTIGRAVITY_LS_ADDRESS}/exa.language_server_pb.LanguageServerService/JetboxWriteState" \
        -H "Content-Type: application/json" \
        -H "x-codeium-csrf-token: ${ANTIGRAVITY_CSRF_TOKEN}" \
        -d '{"userConfig":{"plugins":{"gemini-notebook":{"enabled":true}}}}' >/dev/null 2>&1 || true
    echo "  ✓ Enabled 'gemini-notebook' (Gemini Notebook by Chryzalide) via Antigravity 2 Language Server"
fi
echo ""

# -----------------------------------------------------------------------------
# Step 4/5: Authentication Check (Engine 1 GCP REST / Engine 2 RPC Bridge)
# -----------------------------------------------------------------------------
echo "[Step 4/5] Verifying Gemini Notebook authentication..."
SETUP_READY="$("${VENV_PY}" -c '
import asyncio
from agy_gemini_notebook.mcp_server import notebook_status
st = asyncio.run(notebook_status())
print("1" if st.get("setup_ready") else "0")
')"

if [[ "${SETUP_READY}" == "1" && "${RUN_LOGIN}" -eq 0 ]]; then
    echo "  ✓ Authentication already configured and ready!"
else
    if [[ "${RUN_LOGIN}" -eq 1 ]]; then
        echo "  → Launching interactive Google NotebookLM browser sign-in..."
        "${VENV_CLI}" login
    elif [[ "${CHECK_ONLY}" -eq 0 && -t 0 ]]; then
        echo "  ! No active Gemini Notebook session found yet."
        echo "    Choose an authentication method:"
        echo "      [1] Personal / Workspace Google Account (notebooklm.google.com — opens browser)"
        echo "      [2] Google Cloud NotebookLM Enterprise (gcloud auth login + GCP_PROJECT_NUMBER)"
        echo "      [3] Skip for now (authenticate later from the IDE or CLI)"
        read -r -p "    Select [1/2/3] (default: 1): " AUTH_CHOICE
        AUTH_CHOICE="${AUTH_CHOICE:-1}"
        if [[ "${AUTH_CHOICE}" == "1" ]]; then
            "${VENV_CLI}" login
        elif [[ "${AUTH_CHOICE}" == "2" ]]; then
            echo "    Run: export GCP_PROJECT_NUMBER=\"YOUR_PROJECT_NUMBER\" && gcloud auth login"
        else
            echo "    Skipped authentication (run './scripts/setup.sh --login' or '.venv/bin/agy-gemini-notebook login' anytime)."
        fi
    else
        echo "  ! Authentication not yet completed."
        echo "    To sign in with your Google account, run:"
        echo "      ${VENV_CLI} login"
        echo "    Or click '🔑 Sign In to Google NotebookLM' inside the Sidecar panel."
    fi
fi
echo ""

# -----------------------------------------------------------------------------
# Step 5/5: Final Diagnostic Status & Next Steps
# -----------------------------------------------------------------------------
echo "[Step 5/5] Current Plugin & Workspace Status:"
"${VENV_CLI}" status
echo ""
echo "======================================================================"
echo " ✅ Setup workflow finished!"
echo " 👉 Next Steps in Antigravity 2:"
echo "    1. Reload or restart Antigravity 2 once so the Sidecar UI server"
echo "       ('Gemini Notebook by Chryzalide') and MCP server reload."
echo "    2. Open 'Overview -> Plugins -> Gemini Notebook by Chryzalide'"
echo "       (or click the sidecar pill link in chat)."
echo "    3. If not signed in yet, click '🔑 Sign In to Google NotebookLM'"
echo "       or run: .venv/bin/agy-gemini-notebook login"
echo "======================================================================"
