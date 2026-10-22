---
name: gemini-notebook
description: Connect Antigravity 2 conversations and workspaces to Gemini Notebooks (NotebookLM & NotebookLM Enterprise). Use when the user wants to list, attach, query, sync files to, or generate audio overviews from a Gemini Notebook / NotebookLM.
---

# Gemini Notebook (NotebookLM) Integration Skill

Use this skill whenever the user mentions **Gemini Notebook**, **NotebookLM**, **attaching a notebook for context**, **querying notebook sources**, or **syncing project documentation to NotebookLM**.

## Available MCP Tools (`gemini_notebook_mcp`)

1. **Inspect & Attach Workspace Context**
   - `notebook_status`: Check which notebook is currently bound to the workspace (`.agents/notebook.json`) and check engine authentication status (`gcp_enterprise_rest` and `rpc_bridge`).
   - `notebook_list`: List available notebooks (`listRecentlyViewed` via Google Cloud Discovery Engine `v1alpha` or via RPC bridge).
   - `notebook_get`: Retrieve details and the list of `sources` for a specific `notebook_id`.
   - `notebook_attach`: Bind a `notebook_id` (and optional list of active `source_ids`) to the current workspace in `.agents/notebook.json`.

2. **Query Context (Notebook $\rightarrow$ Antigravity 2)**
   - `notebook_query`: Ask a grounded question against a notebook (optionally filtered to specific `source_ids`) and receive a synthesized answer with source citations.
   - `notebook_get_source_text`: Fetch the full indexed text of a specific source inside a notebook.

3. **Push & Provision Context (Antigravity 2 $\rightarrow$ NotebookLM Enterprise `v1alpha`)**
   - `notebook_create`: Create a new Gemini Notebook in Google Cloud Discovery Engine.
   - `notebook_sync_files`: Upload local workspace files (`.md`, `.pdf`, `.txt`, `.docx`, `.pptx`, `.xlsx`, audio, images) to a notebook using the official `sources:uploadFile` endpoint.
   - `notebook_add_source`: Add raw text/markdown (`text`), a web URL (`web`), a YouTube URL (`youtube`), or a Google Drive document (`gdrive`) via `sources:batchCreate`.
   - `notebook_create_audio`: Trigger generation of a podcast Audio Overview (`audioOverviews:create`) with a custom `episode_focus` and `language_code` (e.g., `"en"`).

## Recommended Workflows

### Workflow A: Grounding a Coding Task in a Gemini Notebook
1. Call `notebook_status` to check if a notebook is already attached to the workspace.
2. If not attached, call `notebook_list` to show the user their available notebooks, then call `notebook_attach` with the chosen `notebook_id`.
3. Call `notebook_query` with a focused question about the architecture, API specs, or business rules needed for the coding task.
4. Surface the sidecar link `[Open Gemini Notebook Panel](sidecar://gemini-notebook/notebook_context_panel)` so the user can view or toggle active sources.

### Workflow B: Syncing Repository Docs to a Notebook
1. Identify the local documentation files (e.g., `README.md`, `AGENTS.md`, `docs/*.md`).
2. Call `notebook_sync_files` with the target `notebook_id` and the list of absolute file paths.
3. Optionally call `notebook_create_audio` if the user wants an audio briefing of the newly synced documentation.
