"""FastMCP Server for the Antigravity 2 Gemini Notebook Plugin (`gemini_notebook_mcp`)."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from agy_gemini_notebook.config import (
    WorkspaceNotebookBinding,
    describe_environment,
    load_workspace_binding,
    save_workspace_binding,
)
from agy_gemini_notebook.engines.gcp_enterprise_rest import GcpNotebookEnterpriseClient
from agy_gemini_notebook.engines.rpc_bridge import NotebookRpcBridge

mcp = FastMCP(
    "gemini_notebook_mcp",
    instructions=(
        "Connect Antigravity 2 conversations and workspaces to Gemini Notebooks "
        "(NotebookLM & NotebookLM Enterprise). Supports official Google Cloud v1alpha "
        "Discovery Engine REST API for notebook/source management and RPC bridge for grounded Q&A."
    ),
)


def _resolve_notebook_id(notebook_id: str | None = None) -> str:
    """Resolve `notebook_id` from explicit argument or workspace `.agents/notebook.json`."""
    if notebook_id:
        return notebook_id
    binding = load_workspace_binding()
    if binding and binding.notebook_id:
        return binding.notebook_id
    raise ValueError(
        "No `notebook_id` provided and no notebook is currently attached in `.agents/notebook.json`. "
        "Call `notebook_list` to find a notebook or `notebook_attach` to bind one to this workspace."
    )


@mcp.tool()
async def notebook_status(workspace_dir: str | None = None) -> dict[str, Any]:
    """Check current workspace Gemini Notebook binding and authentication status for both engines."""
    info = describe_environment(workspace_dir)
    info["rpc_bridge"] = {"notebooklm_py_installed": NotebookRpcBridge.is_available()}
    return info


@mcp.tool()
async def notebook_attach(
    notebook_id: str,
    title: str | None = None,
    active_source_ids: list[str] | None = None,
    gcp_project_number: str | None = None,
    workspace_dir: str | None = None,
) -> dict[str, Any]:
    """Attach a Gemini Notebook to the active Antigravity 2 workspace (`.agents/notebook.json`)."""
    binding = WorkspaceNotebookBinding(
        notebook_id=notebook_id,
        title=title,
        active_source_ids=active_source_ids or [],
        gcp_project_number=gcp_project_number,
    )
    saved_path = save_workspace_binding(binding, workspace_dir)
    return {
        "status": "attached",
        "binding_file": str(saved_path),
        "binding": binding.model_dump(exclude_none=True),
        "sidecar_link": "sidecar://gemini-notebook/notebook_context_panel",
    }


@mcp.tool()
async def notebook_list(page_size: int = 25, engine: str = "auto") -> dict[str, Any]:
    """List recently viewed Gemini Notebooks via official GCP v1alpha REST API or RPC bridge."""
    client = GcpNotebookEnterpriseClient()
    if engine in ("auto", "gcp_rest") and client.project_number:
        notebooks = await client.list_recently_viewed(page_size=page_size)
        return {"engine": "gcp_enterprise_rest", "notebooks": notebooks}

    bridge = NotebookRpcBridge()
    notebooks = await bridge.list_notebooks()
    return {"engine": "rpc_bridge", "notebooks": notebooks[:page_size]}


@mcp.tool()
async def notebook_get(notebook_id: str | None = None) -> dict[str, Any]:
    """Get metadata and list of sources for a Gemini Notebook via official GCP v1alpha REST API."""
    target_id = _resolve_notebook_id(notebook_id)
    client = GcpNotebookEnterpriseClient()
    return await client.get_notebook(target_id)


@mcp.tool()
async def notebook_create(title: str, attach_to_workspace: bool = True) -> dict[str, Any]:
    """Create a new Gemini Notebook via official GCP v1alpha REST API (`notebooks.create`)."""
    client = GcpNotebookEnterpriseClient()
    created = await client.create_notebook(title=title)
    notebook_id = created.get("notebookId") or created.get("name", "").split("/")[-1]
    if attach_to_workspace and notebook_id:
        save_workspace_binding(
            WorkspaceNotebookBinding(
                notebook_id=notebook_id,
                title=title,
                gcp_project_number=client.project_number,
            )
        )
    return created


@mcp.tool()
async def notebook_sync_files(
    file_paths: list[str],
    notebook_id: str | None = None,
) -> dict[str, Any]:
    """Upload local workspace files (.md, .pdf, .txt, .docx, etc.) to a Gemini Notebook via official `sources:uploadFile`."""
    target_id = _resolve_notebook_id(notebook_id)
    client = GcpNotebookEnterpriseClient()
    results: list[dict[str, Any]] = []
    for fpath in file_paths:
        res = await client.upload_file_source(notebook_id=target_id, file_path=fpath)
        results.append({"file": fpath, "result": res})
    return {"notebook_id": target_id, "uploaded": results}


@mcp.tool()
async def notebook_add_source(
    source_type: str,
    value: str,
    title: str = "Workspace Source",
    mime_type: str = "application/vnd.google-apps.document",
    notebook_id: str | None = None,
) -> dict[str, Any]:
    """Add a text, web URL, YouTube video, or Google Drive document to a Gemini Notebook via `sources:batchCreate`.

    Args:
        source_type: One of 'text', 'web', 'youtube', or 'gdrive'.
        value: Raw text content, URL, YouTube URL, or Google Drive documentId.
        title: Display name for the source.
        mime_type: Used only when `source_type == 'gdrive'`.
        notebook_id: Target notebook ID (defaults to workspace-attached notebook).
    """
    target_id = _resolve_notebook_id(notebook_id)
    client = GcpNotebookEnterpriseClient()

    if source_type == "text":
        user_content = {"textContent": {"sourceName": title, "content": value}}
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

    return await client.batch_create_sources(
        notebook_id=target_id,
        user_contents=[user_content],
    )


@mcp.tool()
async def notebook_create_audio(
    episode_focus: str = "",
    language_code: str = "en",
    source_ids: list[str] | None = None,
    notebook_id: str | None = None,
) -> dict[str, Any]:
    """Generate an Audio Overview podcast on the notebook via official `audioOverviews.create`."""
    target_id = _resolve_notebook_id(notebook_id)
    client = GcpNotebookEnterpriseClient()
    return await client.create_audio_overview(
        notebook_id=target_id,
        episode_focus=episode_focus,
        language_code=language_code,
        source_ids=source_ids,
    )


@mcp.tool()
async def notebook_query(
    question: str,
    notebook_id: str | None = None,
    source_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Ask a grounded question against the attached Gemini Notebook and receive citations."""
    target_id = _resolve_notebook_id(notebook_id)
    if source_ids is None:
        binding = load_workspace_binding()
        if binding and binding.notebook_id == target_id and binding.active_source_ids:
            source_ids = binding.active_source_ids

    bridge = NotebookRpcBridge()
    return await bridge.query_notebook(
        notebook_id=target_id,
        question=question,
        source_ids=source_ids,
    )


@mcp.tool()
async def notebook_get_source_text(
    source_id: str,
    notebook_id: str | None = None,
) -> dict[str, Any]:
    """Fetch the full indexed text of a specific source from a Gemini Notebook."""
    target_id = _resolve_notebook_id(notebook_id)
    bridge = NotebookRpcBridge()
    return await bridge.get_source_fulltext(notebook_id=target_id, source_id=source_id)


def main() -> None:
    """Entrypoint for the `agy-gemini-notebook-mcp` CLI command."""
    mcp.run()


if __name__ == "__main__":
    main()
