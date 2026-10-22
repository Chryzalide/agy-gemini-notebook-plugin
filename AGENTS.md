# Project Context & Architecture Guide (`agy-gemini-notebook-plugin`)

> **Note for Antigravity 2 Agents:** This repository is an open-source **Antigravity 2 Plugin** designed to bridge **Gemini Notebooks (NotebookLM & NotebookLM Enterprise)** with Antigravity 2 conversations and workspaces.

---

## 1. Problem Statement & Vision

In the consumer **Gemini Web** app, users can attach a **Gemini Notebook (NotebookLM)** as grounded context for their conversation. However, **Antigravity 2** (which is a far more capable agentic coding harness) does not natively offer a "Connect to Gemini Notebook" button.

This open-source plugin brings **first-class Gemini Notebook integration** to Antigravity 2 by combining:
1. **An MCP Server (`gemini_notebook_mcp`)** exposing tools to list, create, attach, sync workspace files into, and query Gemini Notebooks.
2. **An Antigravity 2 Skill (`skills/gemini-notebook/SKILL.md`) & Rule (`rules/AGENTS.md`)** so the agent knows how to automatically ground its answers in the workspace's active notebook (`.agents/notebook.json`).
3. **An Antigravity 2 Sidecar UI (`sidecars/notebook_context_panel/`)** in the Auxiliary Pane (`SIDECAR_UI_ENTRYPOINT_AUX_PANE`) allowing 1-click selection of active notebooks and source toggles (`sidecar://gemini-notebook/notebook_context_panel`).

---

## 2. Dual-Engine Architecture (Key Research Findings)

Our technical investigation revealed two complementary APIs that this plugin unifies under a **Dual-Engine** design:

### Engine 1: Official Google Cloud REST API (`v1alpha` Discovery Engine — NotebookLM Enterprise)
- **Documentation References:**
  - Manage Notebooks: `https://docs.cloud.google.com/gemini/enterprise/notebooklm-enterprise/docs/api-notebooks`
  - Manage Sources: `https://docs.cloud.google.com/gemini/enterprise/notebooklm-enterprise/docs/api-notebooks-sources`
  - REST Reference: `https://docs.cloud.google.com/gemini/enterprise/docs/reference/rest/v1alpha/projects.locations.notebooks`
  - RPC Reference: `https://docs.cloud.google.com/gemini/enterprise/docs/reference/rpc/google.cloud.notebooklm.v1alpha`
- **Base URL:**
  `https://{ENDPOINT_LOCATION}-discoveryengine.googleapis.com/v1alpha/projects/{PROJECT_NUMBER}/locations/{LOCATION}/notebooks`
  *(where `ENDPOINT_LOCATION` is `us`, `eu`, or `global`, and `LOCATION` is e.g. `us`, `eu`, `global`)*.
- **Authentication:**
  Standard OAuth 2.0 Bearer token via `gcloud auth print-access-token` (or `gcloud auth login --enable-gdrive-access` when ingesting Google Drive documents). Requires IAM role `Cloud NotebookLM User` (`roles/discoveryengine.notebooklmUser`).
- **Capabilities Exposed by Official REST `v1alpha`:**
  - `notebooks.create` (`POST .../notebooks`)
  - `notebooks.get` (`GET .../notebooks/{notebookId}`)
  - `notebooks.listRecentlyViewed` (`GET .../notebooks:listRecentlyViewed?pageSize=...`)
  - `notebooks.batchDelete` (`POST .../notebooks:batchDelete`)
  - `notebooks.share` (`POST .../notebooks/{notebookId}:share`)
  - `notebooks.sources.batchCreate` (`POST .../notebooks/{notebookId}/sources:batchCreate`) — supports `textContent`, `webContent`, `googleDriveContent`, `videoContent` (YouTube).
  - `notebooks.sources.uploadFile` (`POST https://{ENDPOINT_LOCATION}-discoveryengine.googleapis.com/upload/v1alpha/.../notebooks/{notebookId}/sources:uploadFile`) — raw binary upload (`X-Goog-Upload-Protocol: raw`, `X-Goog-Upload-File-Name`) supporting `.pdf`, `.txt`, `.md`, `.docx`, `.pptx`, `.xlsx`, audio (`.mp3`, `.wav`, `.m4a`), and images (`.png`, `.jpg`).
  - `notebooks.sources.get` & `notebooks.sources.batchDelete`
  - `notebooks.audioOverviews.create` & `notebooks.audioOverviews.delete`
- **Important Limitation of Official `v1alpha` REST API:**
  The official `notebooks` resource in `v1alpha` is currently a **Provisioning, Source Ingestion & Audio Overview API**. It does **not** yet expose a `:query` or `:converse` method on the `notebooks` resource itself, and `sources.get` returns only metadata (`wordCount`, `tokenCount`, `status`), not raw source text.

### Engine 2: RPC Bridge (`rpc_bridge.py` — Consumer & Enterprise Grounded Q&A)
- To support **conversational RAG (`notebook_query`)** with inline citations, **full-text extraction (`notebook_get_source_text`)**, and **consumer Google accounts (`notebooklm.google.com`)**, Engine 2 wraps the community RPC protocol (`batchexecute` / `Orchestrate` via `notebooklm-py` or inspired by `jacob-bd/gemini-notebook-mcp-cli`).
- Additionally, for pure Google Cloud Enterprise environments, Engine 1 can optionally pair with Discovery Engine's official `servingConfigs:streamAnswer` / `conversations:converse` endpoints.

---

## 3. Repository Layout

```text
agy-gemini-notebook-plugin/
├── AGENTS.md                        # This architectural briefing for Antigravity 2
├── next-steps.md                    # Detailed implementation & testing checklist
├── README.md                        # Public open-source documentation
├── LICENSE                          # MIT License
├── pyproject.toml                   # Python package & MCP server entrypoint
├── plugin.json                      # Antigravity 2 plugin manifest
├── mcp_config.json                  # Antigravity 2 MCP server registration
├── rules/
│   └── AGENTS.md                    # Active rule injected when plugin is enabled
├── skills/
│   └── gemini-notebook/
│       └── SKILL.md                 # Skill instructions for using the Notebook tools
├── sidecars/
│   └── notebook_context_panel/
│       ├── sidecar.json             # Sidecar UI definition (Aux Pane)
│       ├── server.py                # Local HTTP server for the Sidecar UI
│       └── ui.html                  # Interactive Notebook selector & source toggle UI
└── src/
    └── agy_gemini_notebook/
        ├── __init__.py
        ├── config.py                # Env vars & .agents/notebook.json workspace binding
        ├── mcp_server.py            # FastMCP server exposing all notebook tools
        └── engines/
            ├── __init__.py
            ├── gcp_enterprise_rest.py  # Official Google Cloud v1alpha REST client
            └── rpc_bridge.py           # Grounded Q&A & Consumer RPC bridge (notebooklm-py)
```

---

## 4. Next Development Steps (Roadmap)

See **`next-steps.md`** at the repository root for the detailed task-by-task implementation and testing checklist:
1. **Complete Dual-Engine Fallback (`rpc_bridge.py` & `mcp_server.py`)**
2. **Make the Sidecar UI Panel Interactive (`sidecars/notebook_context_panel/`)**
3. **Dependency Setup, Linting & Unit Test Suite (`tests/`)**
4. **Real-World Integration & Staging Validation (`staging` $\rightarrow$ `main`)**


---

## 5. Git Branching, Language & Public Repo Hygiene

Repository: `git@github.com:Chryzalide/agy-gemini-notebook-plugin.git`

- **`develop`**: Default active local branch, continuously synchronized with local development (`origin/develop`).
- **`staging`**: Pre-production branch dedicated to real-world integration and E2E testing (`origin/staging`).
- **`main`**: Stable production branch (`origin/main`).
- **100% English Codebase & Docs**: Even when conversing with the user in French, all files in the repository (code, comments, docstrings, UI, `README.md`, `SKILL.md`, commit messages) MUST be written **100% in English**.
- **Zero Local Path Leaks**: Never commit local machine filesystem paths, usernames, mount points, or private environment details to any tracked file in this public repository.


