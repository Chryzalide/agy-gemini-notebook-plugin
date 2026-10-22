#!/usr/bin/env bash
# Portable MCP Server launcher for Antigravity 2, VS Code, Cursor, and Claude Code.
# Automatically resolves the plugin's virtual environment (.venv), `uv`, or system `python3`.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

if [[ -x "${PLUGIN_DIR}/.venv/bin/python" ]]; then
    exec "${PLUGIN_DIR}/.venv/bin/python" -m agy_gemini_notebook.mcp_server "$@"
fi

if command -v uv >/dev/null 2>&1; then
    exec uv run --project "${PLUGIN_DIR}" agy-gemini-notebook-mcp "$@"
fi

if [[ -x "${HOME}/.local/bin/uv" ]]; then
    exec "${HOME}/.local/bin/uv" run --project "${PLUGIN_DIR}" agy-gemini-notebook-mcp "$@"
fi

export PYTHONPATH="${PLUGIN_DIR}/src:${PYTHONPATH:-}"
exec python3 -m agy_gemini_notebook.mcp_server "$@"
