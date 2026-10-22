# Next Steps & Implementation Roadmap (`agy-gemini-notebook-plugin`)

This document tracks the remaining engineering, testing, and integration tasks required to bring `agy-gemini-notebook-plugin` from its `v0.1.0-alpha` architectural foundation to a fully functional, production-ready Antigravity 2 plugin.

---

## 1. Complete Dual-Engine Fallback (`rpc_bridge.py` & `mcp_server.py`) — ✅ COMPLETED

`GcpNotebookEnterpriseClient` (`src/agy_gemini_notebook/engines/gcp_enterprise_rest.py`) and `NotebookRpcBridge` (`src/agy_gemini_notebook/engines/rpc_bridge.py`) now provide full Dual-Engine parity, multi-IDE portability (Antigravity 2, VS Code, Cursor, Claude Code), and autonomous notebook piloting (including **Fast Research**, **Deep Research**, and **Studio Artifacts**).

### Tasks:
- [x] **Inspect `notebooklm-py` SDK signatures**:
  - Initialized the local virtual environment (`.venv`) and verified exact async methods and return objects exposed by `notebooklm.NotebookLMClient` (`notebooks`, `sources`, `chat`, `research`, `artifacts`, `notes`, `sharing`).
- [x] **Expand `NotebookRpcBridge` (`src/agy_gemini_notebook/engines/rpc_bridge.py`)**:
  - Implemented `get_notebook(notebook_id)`, `create_notebook(title)`, `delete_notebooks(notebook_ids)`, `rename_notebook(notebook_id, new_title)`, and `share_notebook(notebook_id, public, email, role)`.
  - Implemented `upload_file_source(notebook_id, file_path)`, `add_source(notebook_id, source_type, value, title)`, `delete_sources(notebook_id, source_ids)`, and `search_sources(notebook_id, query)`.
  - Implemented `run_research(notebook_id, query, mode="fast"|"deep"|"discover", source="web"|"drive", auto_import=True)`, `poll_research(...)`, and `cancel_research(...)` for autonomous Deep Research and source discovery.
  - Implemented `create_audio_overview(...)`, `generate_artifact(...)` (briefing doc, study guide, mind map, quiz, flashcards, slide deck), and `manage_notes(...)`.
- [x] **Unify Engine Routing & Multi-Environment Support in `mcp_server.py` & `cli.py`**:
  - Unified routing via `_select_engine()` across all 20 MCP tools (`notebook_status`, `notebook_attach`, `notebook_invoke`, `notebook_list`, `notebook_get`, `notebook_create`, `notebook_delete`, `notebook_rename`, `notebook_share`, `notebook_sync_files`, `notebook_add_source`, `notebook_delete_sources`, `notebook_search_sources`, `notebook_get_source_text`, `notebook_query`, `notebook_research`, `notebook_research_status`, `notebook_create_audio`, `notebook_generate_artifact`, `notebook_notes`).
  - Added MCP Resources (`notebook://status`, `notebook://active`, `notebook://info/{target}`) and MCP Prompts (`invoke_notebook`, `deep_research_notebook`) plus `--transport stdio|sse|streamable-http` support and portable `scripts/run_mcp.sh` + `agy-gemini-notebook` CLI (`src/agy_gemini_notebook/cli.py`).
- [x] **Add Enterprise Grounded Q&A Option**:
  - Implemented `GcpNotebookEnterpriseClient.query_enterprise()` pairing Engine 1 with Discovery Engine's `servingConfigs:answer` endpoint when `GCP_ENGINE_ID` is configured.

---

## 2. Make the Sidecar UI Panel Interactive (`sidecars/notebook_context_panel/`) — ✅ COMPLETED

`sidecars/notebook_context_panel/server.py` and `sidecars/notebook_context_panel/ui.html` now provide a full interactive Auxiliary Pane sidecar backed by a multithreaded local JSON API server.

### Tasks:
- [x] **Upgrade `sidecars/notebook_context_panel/server.py`**:
  - Added JSON API endpoints to the local `ThreadingHTTPServer`:
    - `GET /api/status` — Returns `describe_environment()` + `rpc_bridge` auth status + active `.agents/notebook.json` binding + discovered workspace documentation files.
    - `GET /api/notebooks` — Lists and normalizes available notebooks from the active engine (`gcp_enterprise_rest` or `rpc_bridge`).
    - `GET /api/notebook?id=<notebook_id>` — Fetches and normalizes notebook metadata, AI summary, and sources inside the selected notebook.
    - `POST /api/attach` — Saves the selected `notebook_id`, `title`, and `active_source_ids` to `.agents/notebook.json`.
    - `POST /api/sources/toggle` — Updates `active_source_ids` in `.agents/notebook.json` when a user checks or unchecks sources (or resets to all sources) in the UI.
    - `POST /api/sync` & `POST /api/create` — Uploads selected workspace documentation files and creates/attaches new notebooks directly from the Sidecar UI.
- [x] **Upgrade `sidecars/notebook_context_panel/ui.html`**:
  - Added client-side JavaScript (`fetch`) and accessible dark/light UI controls to:
    - Display live authentication status for Engine 1 (GCP REST `v1alpha`) and Engine 2 (RPC Bridge) with an engine mode selector.
    - Render a 1-click notebook switcher dropdown (`#notebook-select`) and inline "+ Create" notebook action.
    - Render accessible custom checkboxes (`.checkbox-custom` with indeterminate "Select all" state) for each source in the active notebook so users can filter which sources ground the agent's responses.
    - Provide a "Sync Workspace Docs" quick action card with selectable workspace documentation chips.

---

## 3. Dependency Setup, Linting & Unit Test Suite (`tests/`) — ✅ COMPLETED

### Tasks:
- [x] **Initialize `uv` Lockfile & Virtual Environment**:
  - Initialized `.venv` and verified clean installation of `mcp`, `httpx`, `pydantic`, `notebooklm-py`, `pytest`, `pytest-asyncio`, and `ruff`.
- [x] **Expand `tests/test_gcp_enterprise_rest.py`**:
  - Added async unit tests using `httpx.MockTransport` covering:
    - `create_notebook`, `get_notebook`, `list_recently_viewed`, `batch_delete_notebooks`, `share_notebook`.
    - `batch_create_sources` (text, web, YouTube, Google Drive payloads), `get_source`, and `batch_delete_sources`.
    - `upload_file_source` (verifying `/upload/v1alpha/.../sources:uploadFile`, `X-Goog-Upload-Protocol: raw`, `X-Goog-Upload-File-Name`, and MIME type resolution across `.md`, `.pdf`, `.pptx`, `.mp3`, `.png`).
    - `create_audio_overview`, `delete_audio_overview`, `query_enterprise`, and MCP tool routing when `GCP_PROJECT_NUMBER` is configured.
- [x] **Add `tests/test_rpc_bridge_and_mcp.py` & `tests/test_sidecar_server.py`**:
  - Mocked `NotebookLMClient` to test Dual-Engine routing, fallback behavior, Deep Research, Studio artifacts, MCP resources/prompts, and CLI `_dispatch` across MCP tools.
  - Added unit and HTTP integration tests for `sidecars/notebook_context_panel/server.py` JSON endpoints and `ui.html`.
- [x] **Run Ruff & Pytest**:
  - Verified `.venv/bin/ruff check .` (`All checks passed!`) and `.venv/bin/pytest` (`16 passed`, 0 warnings/errors).

---

## 4. Real-World Integration & Staging Validation (`staging` branch) — ✅ COMPLETED

### Tasks:
- [x] **Merge `develop` into `staging`**:
  - Merged `develop` into `staging` and pushed to `origin/staging`.
- [x] **Register Plugin in Antigravity 2**:
  - Symlinked the repository into `~/.gemini/config/plugins/gemini-notebook`.
  - Verified plugin discovery via Antigravity Language Server (`GetAllPlugins` RPC) and enabled it (`JetboxWriteState`).
  - Generated and added a square SVG plugin icon (`assets/logo.svg`) and `suggestedPrompts` in `plugin.json`.
- [x] **End-to-End Live Testing**:
  - Verified live MCP stdio JSON-RPC handshake (`scripts/run_mcp.sh`), `agy-gemini-notebook` CLI (`notebook_status`), and Sidecar HTTP server (`sidecars/notebook_context_panel/server.py`).
  - Exposed `[Gemini Notebook](sidecar://gemini-notebook/ui.html)` in the Antigravity 2 Aux Pane.
- [x] **Promote to `main` (Production Release `v0.1.0`)**:
  - Merged `staging` into `main`, tagged `v0.1.0`, pushed to `origin/main`, and switched the local workspace back to `develop`.
