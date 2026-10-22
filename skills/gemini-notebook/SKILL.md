---
name: gemini-notebook
description: Connect Antigravity 2, VS Code, and agentic coding workspaces to Gemini Notebooks (NotebookLM & NotebookLM Enterprise). Use when the user wants to invoke a notebook, list, attach, create, query, search passages, launch Fast or Deep Research, sync files, manage notes, or generate Studio artifacts (Audio Overviews, Study Guides, Mind Maps, Quizzes, Slide Decks).
---

# Gemini Notebook (NotebookLM & NotebookLM Enterprise) Integration Skill

Use this skill whenever the user mentions **Gemini Notebook**, **NotebookLM**, **invoking a notebook**, **attaching a notebook for context**, **launching research or Deep Research in a notebook**, **syncing project documentation to NotebookLM**, or **generating Studio artifacts** (Audio Overviews, Briefing Docs, Study Guides, Mind Maps, Quizzes, Flashcards, Slide Decks).

## Available MCP Tools (`gemini_notebook_mcp`)

### 1. Invoke, Authenticate & Bind Workspace Notebooks
- `notebook_status`: Check which notebook(s) are currently bound to the workspace (`.agents/notebook.json`) and check authentication status for both Engine 1 (`gcp_enterprise_rest`) and Engine 2 (`rpc_bridge`).
- `notebook_login`: Authenticate with Google NotebookLM via an interactive browser window or browser cookie import when `has_saved_session` is `false` or when a tool returns `status: "auth_required"`.
- `notebook_invoke`: High-level tool to invoke a notebook by ID, workspace alias (e.g. `"architecture"`, `"research"`), or fuzzy notebook title. Optionally pass `question` to immediately ask a grounded question and retrieve inline citations.
- `notebook_attach`: Bind a `notebook_id` (and optional `alias` and `active_source_ids`) to the current workspace in `.agents/notebook.json`.
- `notebook_list`: List available notebooks (with optional `query` title filter) via Google Cloud Discovery Engine `v1alpha` or RPC bridge.
- `notebook_get`: Retrieve metadata, AI summary, suggested questions, and normalized `sources` for a notebook (Dual-Engine).

### 2. Pilot Notebooks: Create, Manage & Share (Dual-Engine)
- `notebook_create`: Create a new Gemini Notebook (via GCP `v1alpha` REST or RPC bridge) and optionally attach it to the workspace under an `alias`.
- `notebook_delete`: Delete one or more notebooks (Dual-Engine).
- `notebook_rename`: Rename a notebook and update `.agents/notebook.json` if attached.
- `notebook_share`: Share a notebook with team members (GCP IAM roles) or enable a public share link (RPC bridge).

### 3. Pilot Autonomous Research & Deep Research (Agent $\rightarrow$ NotebookLM)
- `notebook_research`: Launch an autonomous **Fast Research** (`mode="fast"`), **Deep Research** (`mode="deep"`), or synchronous **Source Discovery** (`mode="discover"`) run across the **Web** (`source="web"`) or **Google Drive** (`source="drive"`).
  - Pass `create_notebook_title="..."` to automatically create a brand-new notebook and run research inside it in a single step.
  - When `wait=True` and `import_sources=True` (default), discovered sources (or `cited_only=True` sources) are automatically imported into the notebook and the synthesized Deep Research Markdown `report` is returned.
- `notebook_research_status`: Poll, wait for completion, import discovered sources from, or cancel (`cancel=True`) an ongoing research task by `task_id`.

### 4. Grounded Q&A, Passage Search & Full-Text Extraction (Notebook $\rightarrow$ Agent)
- `notebook_query`: Ask a grounded question against a notebook (optionally filtered to `source_ids`, continuing a `conversation_id`, or saving the cited answer as a persistent note via `save_as_note=True`).
- `notebook_search_sources`: Fast semantic passage search across all indexed sources in the notebook, returning ranked chunks and character spans.
- `notebook_get_source_text`: Fetch the full indexed text (and optional AI summary/keywords `include_guide=True`) of a specific source.

### 5. Push Sources, Generate Studio Artifacts & Manage Notes
- `notebook_sync_files`: Upload local workspace files (`.md`, `.pdf`, `.txt`, `.docx`, `.pptx`, `.xlsx`, audio, images) to a notebook (Dual-Engine).
- `notebook_add_source`: Add raw text (`text`), a web URL (`web`), a YouTube video (`youtube`), or a Google Drive document (`gdrive`) (Dual-Engine).
- `notebook_delete_sources`: Delete one or more sources from a notebook (Dual-Engine).
- `notebook_create_audio`: Generate a podcast Audio Overview (`deep-dive`, `brief`, `critique`, `debate`) (Dual-Engine).
- `notebook_generate_artifact`: Generate Studio Artifacts (`report`, `study_guide`, `briefing_doc`, `blog_post`, `mind_map`, `quiz`, `flashcards`, `slide_deck`, `data_table`, `infographic`, `video`).
- `notebook_notes`: Manage persistent cross-session notes inside the notebook (`action` in `"list"`, `"get"`, `"create"`, `"update"`, `"delete"`).

## Recommended Workflows

### Workflow 0: First-Time Installation, Setup & Authentication
When the user installs **Gemini Notebook by Chryzalide** or asks to set up / configure the plugin:
1. **Check Environment Readiness**: Call `notebook_status` (or run `agy-gemini-notebook setup`).
2. **Authenticate Engine**:
   - **Engine 2 (`notebooklm.google.com` Personal / Workspace Account)**: Call `notebook_login` (or instruct the user to run `./scripts/setup.sh --login` / `.venv/bin/agy-gemini-notebook login` or click **"🔑 Sign In to Google NotebookLM"** in the Sidecar panel).
   - **Engine 1 (Google Cloud NotebookLM Enterprise `v1alpha`)**: Set `GCP_PROJECT_NUMBER` and authenticate via `gcloud auth login`.
3. **Bind or Create a Workspace Notebook**: Call `notebook_list` and `notebook_attach`, or `notebook_create(title="...", attach_to_workspace=True)`.
4. **Open Sidecar UI**: Surface `[Gemini Notebook by Chryzalide](sidecar://gemini-notebook/notebook_context_panel/ui.html)` (note: after a fresh plugin install, remind the user to reload/restart Antigravity 2 once so the Sidecar server starts).

### Workflow A: Invoking a Notebook to Ground a Coding Task
1. Call `notebook_invoke(target="...", question="...")` (or `notebook_status` + `notebook_query`).
2. Cite the notebook title and specific source names in your response.
3. In Antigravity 2, surface `[Gemini Notebook by Chryzalide](sidecar://gemini-notebook/notebook_context_panel/ui.html)` when attaching or updating workspace notebooks.

### Workflow B: Piloting Deep Research into a New or Active Notebook
1. Call `notebook_research(query="...", mode="deep", source="web", wait=True, import_sources=True, create_notebook_title="...")` (or omit `create_notebook_title` to use the attached notebook).
2. Review the synthesized `report` and `imported_sources`.
3. Optionally call `notebook_generate_artifact(artifact_type="study_guide")` or `notebook_create_audio(...)` to produce deliverables from the newly researched corpus.

### Workflow C: Documentary Research for Decision Support $\rightarrow$ Local Project `.md` Files
When the user's project needs documentary research to aid technical, architectural, or product decision-making:
1. **Open or Create a Notebook**: Invoke an existing Gemini Notebook (`notebook_invoke(target="...")`) or create a new one (`notebook_create(title="...", attach_to_workspace=True)`).
2. **Run Research or Query Curated Sources**: Launch documentary research (`notebook_research(query="...", mode="deep", import_sources=True)`) and/or ask targeted questions over the documents in the notebook (`notebook_query` / `notebook_search_sources`).
3. **Create Local `.md` Files in the Project**: Synthesize the relevant, decision-focused findings (with explicit notebook & source citations) into local Markdown files inside the project workspace (e.g., `docs/research/<topic>.md` or `docs/adr/<decision>.md`).
