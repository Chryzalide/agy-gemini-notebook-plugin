# Next Steps & Implementation Roadmap (`agy-gemini-notebook-plugin`)

This document tracks the remaining engineering, testing, and integration tasks required to bring `agy-gemini-notebook-plugin` from its `v0.1.0-alpha` architectural foundation to a fully functional, production-ready Antigravity 2 plugin.

---

## 1. Complete Dual-Engine Fallback (`rpc_bridge.py` & `mcp_server.py`)

Currently, `GcpNotebookEnterpriseClient` (`src/agy_gemini_notebook/engines/gcp_enterprise_rest.py`) implements the full Google Cloud Discovery Engine `v1alpha` REST API, while `NotebookRpcBridge` (`src/agy_gemini_notebook/engines/rpc_bridge.py`) only implements `list_notebooks`, `query_notebook`, and `get_source_fulltext`.

### Tasks:
- [ ] **Inspect `notebooklm-py` SDK signatures**:
  - Initialize the local virtual environment (`uv sync --all-extras`) and verify the exact async methods and return objects exposed by `notebooklm.NotebookLMClient` (`notebooks`, `sources`, `chat`, `artifacts` / audio generation).
- [ ] **Expand `NotebookRpcBridge` (`src/agy_gemini_notebook/engines/rpc_bridge.py`)**:
  - Implement `get_notebook(notebook_id)` (returning notebook metadata + normalized source list).
  - Implement `create_notebook(title)` for personal Google accounts (`notebooklm.google.com`).
  - Implement `upload_file_source(notebook_id, file_path)` via `notebooklm-py`.
  - Implement `add_source(notebook_id, source_type, value, title)` (text, web URL, YouTube, Google Drive) via `notebooklm-py`.
  - Implement `create_audio_overview(notebook_id, episode_focus, language_code, source_ids)` via `notebooklm-py`.
- [ ] **Unify Engine Routing in `mcp_server.py` (`src/agy_gemini_notebook/mcp_server.py`)**:
  - Update `notebook_get`, `notebook_create`, `notebook_sync_files`, `notebook_add_source`, and `notebook_create_audio` so they automatically fall back to `NotebookRpcBridge` when `GCP_PROJECT_NUMBER` is not configured (or when `binding.engine == "rpc_bridge"`).
- [ ] **Add Enterprise Grounded Q&A Option (Optional)**:
  - Investigate pairing `GcpNotebookEnterpriseClient` with Discovery Engine's `servingConfigs:streamAnswer` / `conversations:converse` endpoint when running in a pure Google Cloud Enterprise environment without browser cookies.

---

## 2. Make the Sidecar UI Panel Interactive (`sidecars/notebook_context_panel/`)

Currently, `sidecars/notebook_context_panel/server.py` is a static file server and `ui.html` is a static status card.

### Tasks:
- [ ] **Upgrade `sidecars/notebook_context_panel/server.py`**:
  - Add JSON API endpoints to the local HTTP server:
    - `GET /api/status` — Returns `describe_environment()` + active `.agents/notebook.json` binding.
    - `GET /api/notebooks` — Lists available notebooks from the active engine (`gcp_enterprise_rest` or `rpc_bridge`).
    - `GET /api/notebook?id=<notebook_id>` — Fetches the sources inside the selected notebook.
    - `POST /api/attach` — Saves the selected `notebook_id`, `title`, and `active_source_ids` to `.agents/notebook.json`.
    - `POST /api/sources/toggle` — Updates `active_source_ids` in `.agents/notebook.json` when a user checks or unchecks sources in the UI.
- [ ] **Upgrade `sidecars/notebook_context_panel/ui.html`**:
  - Add client-side JavaScript (`fetch`) to:
    - Display live authentication status for Engine 1 (GCP REST `v1alpha`) and Engine 2 (RPC Bridge).
    - Render a dropdown/list to switch the active workspace notebook in 1 click.
    - Render checkboxes for each source in the active notebook so users can filter which sources ground the agent's responses.
    - Provide a "Sync Workspace Docs" quick action button.

---

## 3. Dependency Setup, Linting & Unit Test Suite (`tests/`)

### Tasks:
- [ ] **Initialize `uv` Lockfile & Virtual Environment**:
  - Run `uv sync --all-extras` and verify clean installation of `mcp`, `httpx`, `pydantic`, `notebooklm-py`, `pytest`, `pytest-asyncio`, and `ruff`.
- [ ] **Expand `tests/test_gcp_enterprise_rest.py`**:
  - Add async unit tests using `httpx.MockTransport` covering:
    - `create_notebook`, `get_notebook`, `list_recently_viewed`, `batch_delete_notebooks`, `share_notebook`.
    - `batch_create_sources` (text, web, YouTube, Google Drive payloads).
    - `upload_file_source` (verifying ` /upload/v1alpha/.../sources:uploadFile`, `X-Goog-Upload-Protocol: raw`, `X-Goog-Upload-File-Name`, and MIME type resolution).
    - `create_audio_overview` & `delete_audio_overview`.
- [ ] **Add `tests/test_rpc_bridge.py` & `tests/test_mcp_server.py`**:
  - Mock `NotebookLMClient` to test Dual-Engine routing and fallback behavior across all 10 MCP tools.
  - Add unit tests for `sidecars/notebook_context_panel/server.py` JSON endpoints.
- [ ] **Run Ruff & Pytest**:
  - Ensure `uv run ruff check .` and `uv run pytest` pass with 0 warnings/errors.

---

## 4. Real-World Integration & Staging Validation (`staging` branch)

Once Steps 1–3 are complete and committed on `develop`:

### Tasks:
- [ ] **Merge `develop` into `staging`**:
  - `git checkout staging && git merge develop && git push origin staging`
- [ ] **Register Plugin in Antigravity 2**:
  - Symlink the repository into `~/.gemini/config/plugins/gemini-notebook`.
  - Verify plugin discovery via Antigravity Language Server (`GetAllPlugins` RPC) and enable it (`JetboxWriteState`).
  - Generate and add a square SVG plugin icon (`assets/logo.svg`) and `suggestedPrompts` in `plugin.json`.
- [ ] **End-to-End Live Testing**:
  - Test `notebook_status`, `notebook_list`, `notebook_attach`, `notebook_sync_files`, and `notebook_query` against a live Gemini Notebook.
  - Test opening `[Open Gemini Notebook Panel](sidecar://gemini-notebook/notebook_context_panel)` in the Antigravity 2 Aux Pane.
- [ ] **Promote to `main` (Production Release `v0.1.0`)**:
  - Once validated on `staging`, merge `staging` into `main`, tag `v0.1.0`,push to `origin/main`, and switch the local workspace back to `develop`.
