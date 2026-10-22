"""Configuration and Workspace Binding Management (.agents/notebook.json)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class WorkspaceNotebookBinding(BaseModel):
    """Represents the Gemini Notebook attached to an Antigravity 2 or VS Code workspace."""

    notebook_id: str = Field(..., description="Unique ID of the primary attached Gemini Notebook")
    title: str | None = Field(default=None, description="Human-readable notebook title")
    engine: str = Field(
        default="auto",
        description="Preferred engine: 'gcp_rest', 'rpc_bridge', or 'auto'",
    )
    active_source_ids: list[str] = Field(
        default_factory=list,
        description="Optional filter of active source IDs within the notebook (empty = all)",
    )
    aliases: dict[str, str] = Field(
        default_factory=dict,
        description="Optional mapping of short alias names to notebook IDs for multi-notebook workspaces",
    )
    gcp_project_number: str | None = Field(default=None)
    gcp_location: str = Field(default="global")
    gcp_endpoint_location: str = Field(default="global")
    gcp_engine_id: str | None = Field(
        default=None,
        description="Optional Discovery Engine app/engine ID for Enterprise Grounded Q&A",
    )


class PluginConfig(BaseModel):
    """Global configuration resolved from environment variables and workspace files."""

    gcp_project_number: str | None = Field(
        default_factory=lambda: os.environ.get("GCP_PROJECT_NUMBER")
    )
    gcp_location: str = Field(
        default_factory=lambda: os.environ.get("GCP_LOCATION", "global")
    )
    gcp_endpoint_location: str = Field(
        default_factory=lambda: os.environ.get("GCP_ENDPOINT_LOCATION", "global")
    )
    gcp_access_token: str | None = Field(
        default_factory=lambda: os.environ.get("GCP_ACCESS_TOKEN")
    )
    gcp_engine_id: str | None = Field(
        default_factory=lambda: (
            os.environ.get("GCP_ENGINE_ID") or os.environ.get("GCP_DATA_STORE_ID")
        )
    )
    notebooklm_profile: str | None = Field(
        default_factory=lambda: os.environ.get("NOTEBOOKLM_PROFILE")
    )
    notebooklm_storage_path: str | None = Field(
        default_factory=lambda: os.environ.get("NOTEBOOKLM_STORAGE_PATH")
    )

    def get_gcloud_token(self) -> str:
        """Retrieve a valid OAuth2 Bearer token from env or `gcloud auth print-access-token`."""
        if self.gcp_access_token:
            return self.gcp_access_token.strip()
        try:
            result = subprocess.run(
                ["gcloud", "auth", "print-access-token"],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            return result.stdout.strip()
        except Exception as exc:
            raise RuntimeError(
                "Unable to obtain Google Cloud access token. "
                "Run `gcloud auth login` or set GCP_ACCESS_TOKEN."
            ) from exc


def resolve_workspace_dir(workspace_dir: str | Path | None = None) -> Path:
    """Resolve the active workspace directory across Antigravity 2, VS Code, or CLI."""
    if workspace_dir:
        return Path(workspace_dir).expanduser().resolve()
    env_ws = os.environ.get("AGY_WORKSPACE_DIR") or os.environ.get("VSCODE_WORKSPACE_FOLDER")
    if env_ws:
        return Path(env_ws).expanduser().resolve()

    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / ".agents" / "notebook.json").is_file():
            return candidate
        if (candidate / ".git").exists():
            return candidate
    return cwd


def get_binding_path(workspace_dir: str | Path | None = None) -> Path:
    """Return the path to `.agents/notebook.json` inside the target workspace."""
    base = Path(workspace_dir) if workspace_dir else resolve_workspace_dir()
    return base / ".agents" / "notebook.json"


def load_workspace_binding(
    workspace_dir: str | Path | None = None,
) -> WorkspaceNotebookBinding | None:
    """Load the attached notebook configuration for the workspace, if present."""
    path = get_binding_path(workspace_dir)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return WorkspaceNotebookBinding.model_validate(data)


def save_workspace_binding(
    binding: WorkspaceNotebookBinding,
    workspace_dir: str | Path | None = None,
) -> Path:
    """Save the attached notebook configuration to `.agents/notebook.json`."""
    path = get_binding_path(workspace_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(binding.model_dump(exclude_none=True), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return path


def describe_environment(workspace_dir: str | Path | None = None) -> dict[str, Any]:
    """Return diagnostic status of engines and current workspace notebook binding."""
    cfg = PluginConfig()
    binding = load_workspace_binding(workspace_dir)
    has_gcloud_token = False
    try:
        has_gcloud_token = bool(cfg.get_gcloud_token())
    except RuntimeError:
        has_gcloud_token = False

    project_num = cfg.gcp_project_number or (binding.gcp_project_number if binding else None)
    engine_id = cfg.gcp_engine_id or (binding.gcp_engine_id if binding else None)

    return {
        "workspace_dir": str(Path(workspace_dir) if workspace_dir else resolve_workspace_dir()),
        "binding_file": str(get_binding_path(workspace_dir)),
        "attached_notebook": binding.model_dump() if binding else None,
        "gcp_enterprise_rest": {
            "configured": bool(project_num),
            "project_number": project_num,
            "location": binding.gcp_location if binding and binding.gcp_location else cfg.gcp_location,
            "endpoint_location": (
                binding.gcp_endpoint_location
                if binding and binding.gcp_endpoint_location
                else cfg.gcp_endpoint_location
            ),
            "engine_id": engine_id,
            "gcloud_authenticated": has_gcloud_token,
        },
    }
