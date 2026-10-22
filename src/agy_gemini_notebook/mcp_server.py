"""MCP Server for the Gemini Notebook Plugin (`gemini_notebook_mcp`).

Designed for Google Antigravity 2, VS Code (GitHub Copilot / Cline / Roo Code / Continue),
Cursor, Claude Code, and any MCP-compatible agent harness.

Unifies two complementary engines under a single Dual-Engine architecture:
1. Engine 1 (`GcpNotebookEnterpriseClient`): Official Google Cloud `v1alpha` Discovery Engine
   Notebooks REST API + optional Enterprise `servingConfigs:answer` Grounded Q&A.
2. Engine 2 (`NotebookRpcBridge`): `notebooklm-py` RPC bridge for consumer & enterprise
   grounded Q&A, passage search, autonomous Fast/Deep Research, Studio artifacts, and full CRUD fallback.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

try:
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:
    from mcp.server.fastmcp import FastMCP  # type: ignore[no-redef]

from agy_gemini_notebook.config import (
    WorkspaceNotebookBinding,
    describe_environment,
    load_workspace_binding,
    save_workspace_binding,
)
from agy_gemini_notebook.engines.gcp_enterprise_rest import GcpNotebookEnterpriseClient
from agy_gemini_notebook.engines.rpc_bridge import (
    NotebookAuthRequiredError,
    NotebookRpcBridge,
)

mcp = FastMCP(
    "gemini_notebook_mcp",
    instructions=(
        "Connect Antigravity 2, VS Code, and agentic coding workspaces to Gemini Notebooks "
        "(NotebookLM & NotebookLM Enterprise). Allows invoking notebooks for grounded Q&A, "
        "searching passages, creating notebooks, syncing local files, launching Fast & Deep "
        "Research across Web/Drive, generating Studio artifacts (Audio Overviews, Study Guides, "
        "Mind Maps, Quizzes, Slide Decks), and managing persistent notes."
    ),
)


SIDECAR_URI = "sidecar://gemini-notebook/notebook_context_panel/ui.html"


def _auth_error_response(exc: Exception, engine: str = "rpc_bridge") -> dict[str, Any]:
    """Return a structured, non-crashing MCP response when authentication is missing."""
    return {
        "status": "auth_required",
        "authenticated": False,
        "engine": engine,
        "error": str(exc),
        "remediation": (
            "Call the `notebook_login` MCP tool to open the Google sign-in browser window, "
            "run `agy-gemini-notebook login` in your terminal, or set `GCP_PROJECT_NUMBER` "
            "with `gcloud auth login` for Google Cloud Enterprise."
        ),
        "sidecar_link": SIDECAR_URI,
    }


def _extract_notebook_id(raw_id: str) -> str:
    """Extract the bare notebook ID if a full resource name `projects/.../notebooks/{id}` is given."""
    cleaned = raw_id.strip()
    if "/notebooks/" in cleaned:
        return cleaned.split("/notebooks/")[-1].split("/")[0]
    return cleaned


def _resolve_notebook_id(
    notebook_id: str | None = None,
    workspace_dir: str | None = None,
) -> str:
    """Resolve `notebook_id` from explicit argument, workspace alias, or `.agents/notebook.json`."""
    binding = load_workspace_binding(workspace_dir)
    if notebook_id:
        candidate = notebook_id.strip()
        if binding and candidate in binding.aliases:
            return _extract_notebook_id(binding.aliases[candidate])
        return _extract_notebook_id(candidate)
    if binding and binding.notebook_id:
        return _extract_notebook_id(binding.notebook_id)
    raise ValueError(
        "No `notebook_id` provided and no notebook is currently attached in `.agents/notebook.json`. "
        "Call `notebook_list` to find a notebook, `notebook_attach` to bind one, or `notebook_create` to create one."
    )


def _select_engine(
    engine: str = "auto",
    workspace_dir: str | None = None,
) -> tuple[str, GcpNotebookEnterpriseClient, NotebookRpcBridge]:
    """Determine which engine ('gcp_enterprise_rest' or 'rpc_bridge') to use for Dual-Engine tools."""
    binding = load_workspace_binding(workspace_dir)
    client = GcpNotebookEnterpriseClient(
        project_number=binding.gcp_project_number if binding and binding.gcp_project_number else None,
        location=binding.gcp_location if binding and binding.gcp_location else None,
        endpoint_location=(
            binding.gcp_endpoint_location if binding and binding.gcp_endpoint_location else None
        ),
        engine_id=binding.gcp_engine_id if binding and binding.gcp_engine_id else None,
    )
    bridge = NotebookRpcBridge()

    pref = (engine or "auto").lower().strip()
    if pref == "auto" and binding and binding.engine and binding.engine != "auto":
        pref = binding.engine.lower().strip()

    if pref in ("gcp_rest", "gcp_enterprise_rest", "gcp", "rest"):
        return "gcp_enterprise_rest", client, bridge
    if pref in ("rpc_bridge", "rpc", "notebooklm"):
        return "rpc_bridge", client, bridge

    # Auto mode: use GCP Enterprise REST when project_number is configured, else RPC bridge
    if client.project_number:
        return "gcp_enterprise_rest", client, bridge
    return "rpc_bridge", client, bridge


async def _resolve_notebook_target_async(
    target: str | None = None,
    engine: str = "auto",
    workspace_dir: str | None = None,
) -> str:
    """Resolve a notebook ID from an explicit ID, workspace alias, or fuzzy notebook title."""
    binding = load_workspace_binding(workspace_dir)
    if not target:
        return _resolve_notebook_id(None, workspace_dir=workspace_dir)

    cleaned = target.strip()
    if binding:
        if cleaned in binding.aliases:
            return _extract_notebook_id(binding.aliases[cleaned])
        if binding.title and binding.title.lower() == cleaned.lower():
            return _extract_notebook_id(binding.notebook_id)

    # Check if `cleaned` matches a notebook title in `notebook_list` when it contains spaces
    # or does not look like a raw UUID/ID
    if " " in cleaned:
        listing = await notebook_list(page_size=50, engine=engine, workspace_dir=workspace_dir)
        notebooks = listing.get("notebooks", [])
        needle = cleaned.lower()
        for nb in notebooks:
            title = (nb.get("title") or "").lower()
            if title == needle:
                nid = nb.get("notebookId") or nb.get("name", "").split("/")[-1]
                if nid:
                    return str(nid)
        for nb in notebooks:
            title = (nb.get("title") or "").lower()
            if needle in title:
                nid = nb.get("notebookId") or nb.get("name", "").split("/")[-1]
                if nid:
                    return str(nid)

    return _extract_notebook_id(cleaned)


# =============================================================================
# 1. Workspace Context & Notebook Invocation Tools
# =============================================================================


@mcp.tool()
async def notebook_status(workspace_dir: str | None = None) -> dict[str, Any]:
    """Check current workspace Gemini Notebook binding and authentication status for both engines."""
    info = describe_environment(workspace_dir)
    bridge = NotebookRpcBridge()
    resolved_storage = bridge.resolve_storage_path()
    has_session = bridge.has_saved_session()
    info["rpc_bridge"] = {
        "notebooklm_py_installed": bridge.is_available(),
        "has_saved_session": has_session,
        "profile": bridge.profile or "default",
        "storage_path": str(resolved_storage) if resolved_storage else None,
    }
    gcp_info = info.get("gcp_enterprise_rest") or {}
    info["setup_ready"] = bool(
        has_session or (gcp_info.get("configured") and gcp_info.get("gcloud_authenticated"))
    )
    info["sidecar_link"] = SIDECAR_URI
    return info


@mcp.tool()
async def notebook_login(
    browser: str = "chromium",
    timeout: int = 300,
    browser_cookies: str | None = None,
    account: str | None = None,
    fresh: bool = False,
) -> dict[str, Any]:
    """Authenticate with Google NotebookLM via an interactive browser window or browser cookie import.

    Call this tool when `notebook_status` reports `has_saved_session: false` or when a notebook
    tool returns `status: 'auth_required'`.
    """
    bridge = NotebookRpcBridge()
    return await bridge.login(
        browser=browser,
        timeout=timeout,
        browser_cookies=browser_cookies,
        account=account,
        fresh=fresh,
    )


@mcp.tool()
async def notebook_attach(
    notebook_id: str,
    title: str | None = None,
    active_source_ids: list[str] | None = None,
    alias: str | None = None,
    engine: str = "auto",
    gcp_project_number: str | None = None,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Attach a Gemini Notebook to the active workspace (`.agents/notebook.json`).

    Supports optional `alias` so multiple notebooks can be registered and invoked by short name
    (e.g. `alias="architecture"` or `alias="research"`).
    """
    existing = load_workspace_binding(workspace_dir)
    aliases = dict(existing.aliases) if existing and existing.aliases else {}
    clean_id = _extract_notebook_id(notebook_id)
    if alias:
        aliases[alias.strip()] = clean_id

    binding = WorkspaceNotebookBinding(
        notebook_id=clean_id,
        title=title or (existing.title if existing and existing.notebook_id == clean_id else None),
        engine=engine if engine != "auto" else (existing.engine if existing else "auto"),
        active_source_ids=active_source_ids if active_source_ids is not None else [],
        aliases=aliases,
        gcp_project_number=gcp_project_number
        or (existing.gcp_project_number if existing else None),
    )
    saved_path = save_workspace_binding(binding, workspace_dir)
    return {
        "status": "attached",
        "binding_file": str(saved_path),
        "binding": binding.model_dump(exclude_none=True),
        "sidecar_link": SIDECAR_URI,
    }


@mcp.tool()
async def notebook_invoke(
    target: str | None = None,
    question: str | None = None,
    include_sources: bool = True,
    engine: str = "auto",
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Invoke a Gemini Notebook by ID, workspace alias, or title to ground the current task.

    - If `question` is provided: executes a grounded query against the target notebook and returns
      the synthesized answer, inline citations, and follow-up suggestions.
    - If `question` is omitted: fetches the notebook's metadata, AI summary, suggested topics,
      and source list so the agent can immediately inspect and use the notebook context.
    """
    try:
        resolved_id = await _resolve_notebook_target_async(
            target=target,
            engine=engine,
            workspace_dir=workspace_dir,
        )
    except NotebookAuthRequiredError as exc:
        return _auth_error_response(exc, engine)

    result: dict[str, Any] = {
        "invoked_notebook_id": resolved_id,
        "target": target or "active_workspace_notebook",
        "sidecar_link": SIDECAR_URI,
    }
    if include_sources or not question:
        details = await notebook_get(
            notebook_id=resolved_id,
            engine=engine,
            workspace_dir=workspace_dir,
        )
        if details.get("status") == "auth_required":
            return details
        result["notebook"] = details
    if question:
        qa = await notebook_query(
            question=question,
            notebook_id=resolved_id,
            engine=engine,
            workspace_dir=workspace_dir,
        )
        if qa.get("status") == "auth_required":
            return qa
        result["query_result"] = qa
    return result


# =============================================================================
# 2. Dual-Engine Notebook CRUD & Sharing Tools
# =============================================================================


@mcp.tool()
async def notebook_list(
    page_size: int = 25,
    query: str | None = None,
    engine: str = "auto",
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """List Gemini Notebooks via official GCP v1alpha REST API or RPC bridge, with optional title filter."""
    selected_engine, client, bridge = _select_engine(engine, workspace_dir)
    try:
        if selected_engine == "gcp_enterprise_rest":
            notebooks = await client.list_recently_viewed(page_size=page_size)
            used_engine = "gcp_enterprise_rest"
        else:
            notebooks = await bridge.list_notebooks()
            used_engine = "rpc_bridge"
    except NotebookAuthRequiredError as exc:
        return _auth_error_response(exc, selected_engine)

    if query:
        needle = query.lower().strip()
        notebooks = [
            nb for nb in notebooks if needle in (nb.get("title") or "").lower()
        ]

    return {
        "engine": used_engine,
        "count": len(notebooks[:page_size]),
        "notebooks": notebooks[:page_size],
    }


@mcp.tool()
async def notebook_get(
    notebook_id: str | None = None,
    engine: str = "auto",
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Get metadata, AI summary, and list of sources for a Gemini Notebook (Dual-Engine)."""
    target_id = _resolve_notebook_id(notebook_id, workspace_dir=workspace_dir)
    selected_engine, client, bridge = _select_engine(engine, workspace_dir)
    try:
        if selected_engine == "gcp_enterprise_rest":
            data = await client.get_notebook(target_id)
            data.setdefault("engine", "gcp_enterprise_rest")
            return data
        return await bridge.get_notebook(target_id)
    except NotebookAuthRequiredError as exc:
        return _auth_error_response(exc, selected_engine)


@mcp.tool()
async def notebook_create(
    title: str,
    attach_to_workspace: bool = True,
    alias: str | None = None,
    emoji: str | None = None,
    engine: str = "auto",
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Create a new Gemini Notebook via GCP v1alpha REST API or RPC bridge and optionally attach it."""
    selected_engine, client, bridge = _select_engine(engine, workspace_dir)
    try:
        if selected_engine == "gcp_enterprise_rest":
            created = await client.create_notebook(title=title)
            created.setdefault("engine", "gcp_enterprise_rest")
        else:
            created = await bridge.create_notebook(title=title, emoji=emoji)
    except NotebookAuthRequiredError as exc:
        return _auth_error_response(exc, selected_engine)

    notebook_id = created.get("notebookId") or created.get("name", "").split("/")[-1]
    if attach_to_workspace and notebook_id:
        existing = load_workspace_binding(workspace_dir)
        aliases = dict(existing.aliases) if existing and existing.aliases else {}
        if alias:
            aliases[alias.strip()] = notebook_id
        save_workspace_binding(
            WorkspaceNotebookBinding(
                notebook_id=notebook_id,
                title=title,
                engine=selected_engine if engine != "auto" else "auto",
                aliases=aliases,
                gcp_project_number=client.project_number,
            ),
            workspace_dir=workspace_dir,
        )
        created["attached_to_workspace"] = True
        created["sidecar_link"] = SIDECAR_URI
    return created


@mcp.tool()
async def notebook_delete(
    notebook_ids: list[str],
    engine: str = "auto",
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Delete one or more Gemini Notebooks via GCP v1alpha `batchDelete` or RPC bridge."""
    clean_ids = [_extract_notebook_id(nid) for nid in notebook_ids]
    selected_engine, client, bridge = _select_engine(engine, workspace_dir)
    try:
        if selected_engine == "gcp_enterprise_rest":
            res = await client.batch_delete_notebooks(clean_ids)
            return {"engine": "gcp_enterprise_rest", "deleted": clean_ids, "result": res}
        return await bridge.delete_notebooks(clean_ids)
    except NotebookAuthRequiredError as exc:
        return _auth_error_response(exc, selected_engine)


@mcp.tool()
async def notebook_rename(
    new_title: str,
    notebook_id: str | None = None,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Rename a Gemini Notebook via the RPC bridge and update `.agents/notebook.json` if attached."""
    target_id = _resolve_notebook_id(notebook_id, workspace_dir=workspace_dir)
    bridge = NotebookRpcBridge()
    try:
        res = await bridge.rename_notebook(target_id, new_title)
    except NotebookAuthRequiredError as exc:
        return _auth_error_response(exc, "rpc_bridge")

    binding = load_workspace_binding(workspace_dir)
    if binding and binding.notebook_id == target_id:
        binding.title = new_title
        save_workspace_binding(binding, workspace_dir=workspace_dir)
    return res


@mcp.tool()
async def notebook_share(
    email: str | None = None,
    role: str = "PROJECT_ROLE_READER",
    public: bool | None = None,
    notify_via_email: bool = True,
    notebook_id: str | None = None,
    engine: str = "auto",
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Share a Gemini Notebook with an email (GCP Enterprise IAM) or toggle public link sharing (RPC)."""
    target_id = _resolve_notebook_id(notebook_id, workspace_dir=workspace_dir)
    selected_engine, client, bridge = _select_engine(engine, workspace_dir)
    try:
        if selected_engine == "gcp_enterprise_rest" and email:
            res = await client.share_notebook(
                notebook_id=target_id,
                email=email,
                role=role,
                notify_via_email=notify_via_email,
            )
            return {"engine": "gcp_enterprise_rest", "notebookId": target_id, "result": res}
        return await bridge.share_notebook(
            notebook_id=target_id,
            public=True if public is None else public,
        )
    except NotebookAuthRequiredError as exc:
        return _auth_error_response(exc, selected_engine)


# =============================================================================
# 3. Dual-Engine Source Ingestion, Sync & Passage Search Tools
# =============================================================================


@mcp.tool()
async def notebook_sync_files(
    file_paths: list[str],
    notebook_id: str | None = None,
    wait: bool = False,
    engine: str = "auto",
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Upload local workspace files (.md, .pdf, .txt, .docx, .pptx, .xlsx, audio, images) to a Gemini Notebook."""
    target_id = _resolve_notebook_id(notebook_id, workspace_dir=workspace_dir)
    selected_engine, client, bridge = _select_engine(engine, workspace_dir)
    results: list[dict[str, Any]] = []
    try:
        for fpath in file_paths:
            if selected_engine == "gcp_enterprise_rest":
                res = await client.upload_file_source(notebook_id=target_id, file_path=fpath)
            else:
                res = await bridge.upload_file_source(
                    notebook_id=target_id,
                    file_path=fpath,
                    wait=wait,
                )
            results.append({"file": fpath, "result": res})
    except NotebookAuthRequiredError as exc:
        return _auth_error_response(exc, selected_engine)

    return {
        "engine": selected_engine,
        "notebook_id": target_id,
        "uploaded": results,
    }


@mcp.tool()
async def notebook_add_source(
    source_type: str,
    value: str,
    title: str = "Workspace Source",
    mime_type: str = "application/vnd.google-apps.document",
    notebook_id: str | None = None,
    wait: bool = False,
    engine: str = "auto",
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Add a text, web URL, YouTube video, or Google Drive document to a Gemini Notebook (Dual-Engine).

    Args:
        source_type: One of 'text', 'web', 'youtube', or 'gdrive'.
        value: Raw text content, URL, YouTube URL, or Google Drive documentId.
        title: Display name for the source.
        mime_type: Used only when `source_type == 'gdrive'`.
        notebook_id: Target notebook ID or workspace alias (defaults to workspace-attached notebook).
        wait: When using RPC bridge, whether to wait for source indexing to complete.
        engine: 'auto', 'gcp_rest', or 'rpc_bridge'.
        workspace_dir: Optional workspace directory override.
    """
    target_id = _resolve_notebook_id(notebook_id, workspace_dir=workspace_dir)
    selected_engine, client, bridge = _select_engine(engine, workspace_dir)

    if selected_engine == "gcp_enterprise_rest":
        if source_type == "text":
            user_content: dict[str, Any] = {
                "textContent": {"sourceName": title, "content": value}
            }
        elif source_type == "web":
            user_content = {"webContent": {"url": value, "sourceName": title}}
        elif source_type == "youtube":
            user_content = {"videoContent": {"youtubeUrl": value}}
        elif source_type == "gdrive":
            user_content = {
                "googleDriveContent": {
                    "documentId": value,
                    "mimeType": mime_type,
                    "sourceName": title,
                }
            }
        else:
            raise ValueError(
                f"Unsupported source_type '{source_type}'. Expected 'text', 'web', 'youtube', or 'gdrive'."
            )

        res = await client.batch_create_sources(
            notebook_id=target_id,
            user_contents=[user_content],
        )
        res.setdefault("engine", "gcp_enterprise_rest")
        return res

    try:
        return await bridge.add_source(
            notebook_id=target_id,
            source_type=source_type,
            value=value,
            title=title,
            mime_type=mime_type,
            wait=wait,
        )
    except NotebookAuthRequiredError as exc:
        return _auth_error_response(exc, "rpc_bridge")


@mcp.tool()
async def notebook_delete_sources(
    source_ids: list[str],
    notebook_id: str | None = None,
    engine: str = "auto",
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Delete one or more sources from a Gemini Notebook (Dual-Engine)."""
    target_id = _resolve_notebook_id(notebook_id, workspace_dir=workspace_dir)
    selected_engine, client, bridge = _select_engine(engine, workspace_dir)
    try:
        if selected_engine == "gcp_enterprise_rest":
            res = await client.batch_delete_sources(notebook_id=target_id, source_ids=source_ids)
            return {
                "engine": "gcp_enterprise_rest",
                "notebookId": target_id,
                "deletedSourceIds": source_ids,
                "result": res,
            }
        return await bridge.delete_sources(notebook_id=target_id, source_ids=source_ids)
    except NotebookAuthRequiredError as exc:
        return _auth_error_response(exc, selected_engine)


@mcp.tool()
async def notebook_search_sources(
    query: str,
    notebook_id: str | None = None,
    source_ids: list[str] | None = None,
    limit: int = 10,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Search indexed passages across a Gemini Notebook's sources and return ranked chunks."""
    target_id = _resolve_notebook_id(notebook_id, workspace_dir=workspace_dir)
    if source_ids is None:
        binding = load_workspace_binding(workspace_dir)
        if binding and binding.notebook_id == target_id and binding.active_source_ids:
            source_ids = binding.active_source_ids

    bridge = NotebookRpcBridge()
    try:
        return await bridge.search_sources(
            notebook_id=target_id,
            query=query,
            source_ids=source_ids,
            limit=limit,
        )
    except NotebookAuthRequiredError as exc:
        return _auth_error_response(exc, "rpc_bridge")


@mcp.tool()
async def notebook_get_source_text(
    source_id: str,
    notebook_id: str | None = None,
    include_guide: bool = False,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Fetch the full indexed text (and optional AI summary/keywords guide) of a specific source."""
    target_id = _resolve_notebook_id(notebook_id, workspace_dir=workspace_dir)
    bridge = NotebookRpcBridge()
    try:
        return await bridge.get_source_fulltext(
            notebook_id=target_id,
            source_id=source_id,
            include_guide=include_guide,
        )
    except NotebookAuthRequiredError as exc:
        return _auth_error_response(exc, "rpc_bridge")


# =============================================================================
# 4. Grounded Conversational Q&A (`notebook_query`)
# =============================================================================


@mcp.tool()
async def notebook_query(
    question: str,
    notebook_id: str | None = None,
    source_ids: list[str] | None = None,
    conversation_id: str | None = None,
    save_as_note: bool = False,
    note_title: str | None = None,
    engine: str = "auto",
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Ask a grounded question against a Gemini Notebook and receive answer + inline citations.

    Automatically filters to `active_source_ids` in `.agents/notebook.json` if `source_ids`
    is not explicitly passed. Supports multi-turn follow-ups via `conversation_id` and
    persisting the cited answer as a notebook note via `save_as_note=True`.
    """
    target_id = _resolve_notebook_id(notebook_id, workspace_dir=workspace_dir)
    binding = load_workspace_binding(workspace_dir)
    if source_ids is None and binding and binding.notebook_id == target_id and binding.active_source_ids:
        source_ids = binding.active_source_ids

    _, client, bridge = _select_engine(engine, workspace_dir)
    pref = (engine or "auto").lower().strip()

    if pref in ("gcp_rest", "gcp_enterprise_rest") and client.engine_id:
        return await client.query_enterprise(question=question, session_id=conversation_id)

    try:
        res = await bridge.query_notebook(
            notebook_id=target_id,
            question=question,
            source_ids=source_ids,
            conversation_id=conversation_id,
            save_as_note=save_as_note,
            note_title=note_title,
        )
    except NotebookAuthRequiredError as exc:
        if client.project_number and client.engine_id:
            return await client.query_enterprise(question=question, session_id=conversation_id)
        return _auth_error_response(exc, "rpc_bridge")

    if res.get("status") == "rpc_unavailable" and client.project_number and client.engine_id:
        return await client.query_enterprise(question=question, session_id=conversation_id)
    return res


# =============================================================================
# 5. Autonomous Web / Drive Research & Deep Research Tools
# =============================================================================


@mcp.tool()
async def notebook_research(
    query: str,
    mode: str = "fast",
    source: str = "web",
    wait: bool = True,
    import_sources: bool = True,
    cited_only: bool = False,
    timeout: float = 600.0,
    allow_duplicate: bool = False,
    notebook_id: str | None = None,
    create_notebook_title: str | None = None,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Launch a Fast Research, Deep Research, or Source Discovery session on a Gemini Notebook.

    Allows the agent harness to pilot NotebookLM as an autonomous research engine:
    - Set `create_notebook_title` to spin up a brand-new notebook before launching research.
    - Set `mode='fast'` for rapid web/Drive search (~10-30s).
    - Set `mode='deep'` for exhaustive multi-step Deep Research with a synthesized Markdown report.
    - Set `mode='discover'` (or `'curious'`) for instant synchronous web source discovery.
    - Set `source='web'` or `source='drive'`.
    - When `wait=True` and `import_sources=True`, discovered sources are automatically imported
      into the notebook so you can immediately query or generate artifacts from them.
    """
    bridge = NotebookRpcBridge()
    created_notebook: dict[str, Any] | None = None
    if create_notebook_title:
        created_notebook = await notebook_create(
            title=create_notebook_title,
            attach_to_workspace=True,
            engine="rpc_bridge",
            workspace_dir=workspace_dir,
        )
        if created_notebook.get("status") == "auth_required":
            return created_notebook
        target_id = str(
            created_notebook.get("notebookId")
            or created_notebook.get("name", "").split("/")[-1]
        )
    else:
        target_id = _resolve_notebook_id(notebook_id, workspace_dir=workspace_dir)

    try:
        res = await bridge.run_research(
            notebook_id=target_id,
            query=query,
            mode=mode,
            source=source,
            wait=wait,
            import_sources=import_sources,
            cited_only=cited_only,
            timeout=timeout,
            allow_duplicate=allow_duplicate,
        )
    except NotebookAuthRequiredError as exc:
        return _auth_error_response(exc, "rpc_bridge")

    if created_notebook:
        res["created_notebook"] = created_notebook
    return res


@mcp.tool()
async def notebook_research_status(
    task_id: str | None = None,
    wait: bool = False,
    import_sources: bool = False,
    cited_only: bool = False,
    timeout: float = 600.0,
    allow_duplicate: bool = False,
    cancel: bool = False,
    notebook_id: str | None = None,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Check status, wait for completion, import sources from, or cancel a Fast/Deep Research run."""
    target_id = _resolve_notebook_id(notebook_id, workspace_dir=workspace_dir)
    bridge = NotebookRpcBridge()
    try:
        if cancel:
            if not task_id:
                raise ValueError("`task_id` is required when `cancel=True`.")
            return await bridge.cancel_research(notebook_id=target_id, task_id=task_id)

        return await bridge.poll_research(
            notebook_id=target_id,
            task_id=task_id,
            wait=wait,
            timeout=timeout,
            import_sources=import_sources,
            cited_only=cited_only,
            allow_duplicate=allow_duplicate,
        )
    except NotebookAuthRequiredError as exc:
        return _auth_error_response(exc, "rpc_bridge")


# =============================================================================
# 6. Dual-Engine Audio Overview, Studio Artifacts & Persistent Notes
# =============================================================================


@mcp.tool()
async def notebook_create_audio(
    episode_focus: str = "",
    language_code: str = "en",
    source_ids: list[str] | None = None,
    audio_format: str | None = None,
    audio_length: str | None = None,
    wait: bool = False,
    notebook_id: str | None = None,
    engine: str = "auto",
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Generate an Audio Overview podcast on the notebook via GCP v1alpha REST or RPC bridge."""
    target_id = _resolve_notebook_id(notebook_id, workspace_dir=workspace_dir)
    selected_engine, client, bridge = _select_engine(engine, workspace_dir)
    try:
        if selected_engine == "gcp_enterprise_rest":
            res = await client.create_audio_overview(
                notebook_id=target_id,
                episode_focus=episode_focus,
                language_code=language_code,
                source_ids=source_ids,
            )
            res.setdefault("engine", "gcp_enterprise_rest")
            return res

        return await bridge.create_audio_overview(
            notebook_id=target_id,
            episode_focus=episode_focus,
            language_code=language_code,
            source_ids=source_ids,
            audio_format=audio_format,
            audio_length=audio_length,
            wait=wait,
        )
    except NotebookAuthRequiredError as exc:
        return _auth_error_response(exc, selected_engine)


@mcp.tool()
async def notebook_generate_artifact(
    artifact_type: str,
    instructions: str = "",
    report_format: str = "briefing_doc",
    language_code: str = "en",
    source_ids: list[str] | None = None,
    wait: bool = False,
    timeout: float = 300.0,
    notebook_id: str | None = None,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Generate a NotebookLM Studio Artifact (`report`, `study_guide`, `briefing_doc`, `blog_post`, `mind_map`, `quiz`, `flashcards`, `slide_deck`, `data_table`, `infographic`, `video`, `audio`)."""
    target_id = _resolve_notebook_id(notebook_id, workspace_dir=workspace_dir)
    bridge = NotebookRpcBridge()
    try:
        return await bridge.generate_artifact(
            notebook_id=target_id,
            artifact_type=artifact_type,
            instructions=instructions,
            report_format=report_format,
            language_code=language_code,
            source_ids=source_ids,
            wait=wait,
            timeout=timeout,
        )
    except NotebookAuthRequiredError as exc:
        return _auth_error_response(exc, "rpc_bridge")


@mcp.tool()
async def notebook_notes(
    action: str = "list",
    title: str = "Agent Note",
    content: str = "",
    note_id: str | None = None,
    notebook_id: str | None = None,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Manage persistent notes inside a Gemini Notebook (`action`: 'list', 'get', 'create', 'update', 'delete')."""
    target_id = _resolve_notebook_id(notebook_id, workspace_dir=workspace_dir)
    bridge = NotebookRpcBridge()
    try:
        return await bridge.manage_notes(
            notebook_id=target_id,
            action=action,
            title=title,
            content=content,
            note_id=note_id,
        )
    except NotebookAuthRequiredError as exc:
        return _auth_error_response(exc, "rpc_bridge")


# =============================================================================
# 7. MCP Resources & Prompts (for VS Code, Antigravity 2, Cursor, Claude)
# =============================================================================


@mcp.resource(
    "notebook://status",
    name="notebook_workspace_status",
    description="Active workspace Gemini Notebook binding and Dual-Engine authentication status.",
    mime_type="application/json",
)
async def resource_notebook_status() -> str:
    status = await notebook_status()
    return json.dumps(status, indent=2, ensure_ascii=False)


@mcp.resource(
    "notebook://active",
    name="active_workspace_notebook",
    description="Metadata, AI summary, and source list for the workspace's active Gemini Notebook.",
    mime_type="application/json",
)
async def resource_active_notebook() -> str:
    details = await notebook_get()
    return json.dumps(details, indent=2, ensure_ascii=False)


@mcp.resource(
    "notebook://info/{target}",
    name="notebook_by_id_or_alias",
    description="Metadata, AI summary, and source list for a specific Gemini Notebook by ID or workspace alias.",
    mime_type="application/json",
)
async def resource_notebook_by_target(target: str) -> str:
    resolved_id = _resolve_notebook_id(target)
    details = await notebook_get(notebook_id=resolved_id)
    return json.dumps(details, indent=2, ensure_ascii=False)


@mcp.prompt(
    name="invoke_notebook",
    description="Invoke a Gemini Notebook by ID, alias, or title to ground the current coding or research task.",
)
def prompt_invoke_notebook(target: str = "", question: str = "") -> str:
    target_clause = f"target='{target}'" if target else "the active workspace notebook"
    question_clause = (
        f" and ask the grounded question: {question!r}"
        if question
        else " to load its summary, suggested topics, and sources into context"
    )
    return (
        f"Call the `notebook_invoke` MCP tool on {target_clause}{question_clause}. "
        "Cite the notebook title and specific source names in your response."
    )


@mcp.prompt(
    name="deep_research_notebook",
    description="Create or select a Gemini Notebook, run Deep Research on a topic, import cited sources, and synthesize findings.",
)
def prompt_deep_research(topic: str, notebook_title: str = "") -> str:
    create_clause = (
        f"create_notebook_title={notebook_title!r}"
        if notebook_title
        else "the active workspace notebook (or create one if none is attached)"
    )
    return (
        f"Use `notebook_research` with query={topic!r}, mode='deep', wait=True, "
        f"import_sources=True on {create_clause}. "
        "Once Deep Research completes and sources are imported, summarize the synthesized report "
        "and cite the discovered sources."
    )


def main(argv: list[str] | None = None) -> None:
    """Entrypoint for the `agy-gemini-notebook-mcp` CLI command.

    Supports `--transport stdio` (default for Antigravity 2, VS Code, Cursor, Claude Code),
    `--transport sse`, and `--transport streamable-http`.
    """
    parser = argparse.ArgumentParser(
        prog="agy-gemini-notebook-mcp",
        description="Run the Gemini Notebook MCP Server (`gemini_notebook_mcp`).",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="MCP transport protocol (default: stdio).",
    )
    args = parser.parse_args(argv)
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
